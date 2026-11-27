from app.config import load_config
from app.dedupe import is_same
from app.scoring import keep, score
from app.sources import RawJob
from app.text import has_term, location_ok, norm_company

CFG = load_config()


def j(title, company="RBC", location="Toronto, ON", desc="", remote=False):
    return RawJob(source="t", external_id=title, title=title, company=company, url="",
                  location=location, is_remote=remote, description=desc)


def d(job):
    return {"title": job.title, "company": job.company, "location": job.location,
            "is_remote": job.is_remote, "description": job.description}


def test_word_boundaries():
    assert has_term("AI Intern", "ai")
    assert not has_term("Maintenance Intern", "ai")


def test_company_normalization():
    assert norm_company("Royal Bank of Canada") == norm_company("RBC")
    assert norm_company("Shopify Inc.") == norm_company("Shopify")


def test_location_filter():
    assert location_ok("Mississauga, ON")
    assert location_ok("Remote", True)
    assert location_ok("Remote - Canada")
    assert not location_ok("Remote - US")
    assert not location_ok("San Francisco, CA")


def test_keep_filters():
    assert keep(j("Data Analyst Intern"), CFG)
    assert keep(j("Software Engineering Co-op (Winter 2027)"), CFG)
    assert not keep(j("Marketing Intern"), CFG)
    assert not keep(j("Senior Data Analyst"), CFG)
    assert not keep(j("Data Analyst Intern", location="New York, NY"), CFG)


def test_same_job_across_sites():
    a = j("Data Analyst Intern - Summer 2027", "Royal Bank of Canada", "Toronto, ON, Canada")
    b = j("Data Analyst Internship", "RBC", "Toronto, Ontario")
    assert is_same(d(a), d(b))


def test_gta_cities_merge():
    a = j("Software Developer Co-op", "Geotab", "Oakville, ON")
    b = j("Software Developer Intern", "Geotab Inc.", "Mississauga, ON")
    assert is_same(d(a), d(b))


def test_different_jobs_not_merged():
    a = j("Data Analyst Intern", "RBC")
    b = j("Software Developer Intern", "RBC")
    assert not is_same(d(a), d(b))
    c = j("Data Analyst Intern", "TD")
    assert not is_same(d(a), d(c))
    v = j("Data Analyst Intern", "RBC", "Vancouver, BC")
    assert not is_same(d(a), d(v))


def test_plurals_and_dashes():
    assert has_term("12–16 Months", "16 month")
    assert has_term("an 8-month term", "8 month")


def test_subset_titles_not_merged():
    a = j("Business Analyst co-op", "CIBC")
    b = j("Business Enablement Analyst co-op", "CIBC")
    assert not is_same(d(a), d(b))


def test_long_term_and_off_field_penalized():
    long_term, _ = score(j("AI Engineer Intern (16 Months)", "RBC"), CFG)
    fits, _ = score(j("AI Engineer Intern (Winter 2027)", "RBC"), CFG)
    tax, _ = score(j("Taxation Analyst Co-op (Winter 2027)", "RBC"), CFG)
    assert long_term < fits - 30
    assert tax < fits - 20
    assert not keep(j("Master's Machine Learning Engineer, Intern"), CFG)


def test_score_prefers_fit():
    good, _ = score(j("Data Analyst Intern", "RBC", "Toronto, ON",
                      "4-month Winter 2027 work term using Python, SQL and Excel."), CFG)
    bad, _ = score(j("Data Analyst Intern", "Unknown Co", "Calgary, AB",
                     "16-month co-op starting September 2027."), CFG)
    assert good >= 70 and bad < 30


def test_grad_only_postings_are_dropped():
    from app.scoring import grad_only
    assert grad_only("Currently pursuing a Masters or PhD degree at a university in Canada (required)")
    assert grad_only("We are looking for MSc and PhD students in Computer Science")
    assert grad_only("Currently enrolled in a graduate degree program")
    assert not grad_only("Currently enrolled in a Bachelor's or Master's program")
    assert not grad_only("Undergraduate or graduate students welcome")
    assert not grad_only("Recent graduates welcome. Join our Graduate Program.")
    assert not grad_only(None)


def test_project_link_on_resume(tmp_path):
    from docx import Document
    from app.profile import load_profile
    from app.documents import write_resume
    p = load_profile()
    tang = next(e for e in p.entries.values() if e.link)
    assert tang.link.startswith("https://github.com/")
    draft = {"summary": "x", "skills": [{"category": "Languages", "items": ["Python"]}],
             "sections": [{"entry_id": tang.id, "bullets": [{"text": "Did a thing.", "sources": [f"{tang.id}.b0"]}]}]}
    out = tmp_path / "r.docx"
    write_resume(draft, p, out)
    xml = Document(out).part.rels
    assert any("datathon-2026" in r.target_ref for r in xml.values())


def test_hidden_projects_never_reach_resumes():
    from app.profile import load_profile
    p = load_profile()
    assert not any("Hidden Demo" in e.heading for e in p.entries.values())
    assert "Hidden Demo" not in p.as_prompt()
