from pathlib import Path

import pytest

from app import prefill
from app.profile import load_profile

FORMS = Path(__file__).parent / "forms"


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        yield b
        b.close()


@pytest.fixture
def values(tmp_path):
    resume, letter = tmp_path / "Alex_Taylor_Resume.pdf", tmp_path / "Alex_Taylor_Cover_Letter.pdf"
    resume.write_bytes(b"%PDF-1.4 test"); letter.write_bytes(b"%PDF-1.4 test")
    return prefill.build_values(load_profile().raw, [str(resume), str(letter)], "Dear Hiring Manager,\n\nTest letter.")


def open_form(browser, name):
    page = browser.new_page()
    page.goto((FORMS / name).as_uri())
    return page


def test_greenhouse_style_form(browser, values):
    page = open_form(browser, "greenhouse_like.html")
    r = prefill.fill_page(page, values)
    v = lambda sel: page.eval_on_selector(sel, "e => e.value")
    assert v("#first_name") == "Alex" and v("#last_name") == "Taylor"
    assert v("#email") == "alex.taylor@example.com" and v("#phone") == "555-010-0199"
    assert "linkedin.com" in v("#li")
    assert v("#auth") == "Yes"                               # Canada work authorization
    assert page.is_checked('input[name=sp][value="0"]')       # no sponsorship needed in Canada
    assert v("#ussp") == "Select..."                          # U.S. question left for you
    assert v("#hear") == "Company Website"
    assert v("#gender") == "Male" and v("#race") == "East Asian"   # not "Female" (contains "male")
    assert v("#vet") == "I am not a protected veteran"
    assert page.eval_on_selector("#resume", "e => e.files[0].name") == "Alex_Taylor_Resume.pdf"
    assert page.eval_on_selector("#cover", "e => e.files[0].name") == "Alex_Taylor_Cover_Letter.pdf"
    # Things it must NOT do
    assert v("#company") == ""                                # not mistaken for your name
    assert v("#typed") == "already typed by you"              # never overwrites
    assert v("#pw") == ""                                     # never passwords
    assert not page.is_checked("#consent")                    # never ticks consent boxes
    assert not page.evaluate("window.submitted === true")     # never submits
    assert any("Why do you want" in n for n in r["needs_you"])
    assert any("United States" in n for n in r["needs_you"])


def test_lever_style_form(browser, values):
    page = open_form(browser, "lever_like.html")
    prefill.fill_page(page, values)
    v = lambda sel: page.eval_on_selector(sel, "e => e.value")
    assert v("[name=name]") == "Alex Taylor"
    assert v("[name=email]") == "alex.taylor@example.com"
    assert v("[name=location]") == "Mississauga, ON"
    assert "github.com" in v('[name="urls[GitHub]"]')
    assert v("[name=comments]").startswith("Dear Hiring Manager")   # cover letter box
    assert page.eval_on_selector("[name=resume]", "e => e.files[0].name") == "Alex_Taylor_Resume.pdf"
    assert not page.evaluate("window.submitted === true")


def test_panel_injects_without_breaking_page(browser, values):
    page = browser.new_page()
    page.add_init_script(prefill.panel_js(values))
    page.goto((FORMS / "lever_like.html").as_uri())
    page.wait_for_selector("#jobapp-panel-host", state="attached")
    # panel fields are ignored by the filler
    r = prefill.fill_page(page, values)
    assert not any("JobApp" in x for x in r["filled"])


def test_apply_url_goes_straight_to_form():
    gh = [{"source": "linkedin", "external_id": "1", "url": "https://linkedin.com/x"},
          {"source": "greenhouse", "external_id": "stripe:123", "url": "https://stripe.com/jobs?gh_jid=123"}]
    assert prefill.apply_url(gh) == "https://job-boards.greenhouse.io/stripe/jobs/123"
    lv = [{"source": "lever", "external_id": "w:abc", "url": "https://jobs.lever.co/wattpad/abc"}]
    assert prefill.apply_url(lv).endswith("/abc/apply")
