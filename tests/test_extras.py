import json

import pytest

from app import ats_check, db, documents, followups, outreach, stats
from app.profile import load_profile

P = load_profile()


@pytest.fixture
def tmpdb(tmp_path, monkeypatch):
    import app.db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    return tmp_path


def add_job(c, jid, title, company, status, applied_days_ago=None, source="workday"):
    applied = f"datetime('now', '-{applied_days_ago} days')" if applied_days_ago is not None else "NULL"
    c.execute(f"INSERT INTO jobs (id, title, company, found_at, status, applied_at, score) "
              f"VALUES (?, ?, ?, datetime('now'), ?, {applied}, 80)", (jid, title, company, status))
    c.execute("INSERT INTO job_links (job_id, source, external_id, url, first_seen, last_seen) "
              "VALUES (?, ?, ?, 'u', 'x', 'x')", (jid, source, str(jid)))
    db.record_status(c, jid, status)


def test_ats_readback_on_real_pdf(tmp_path):
    exp = next(e for e in P.entries.values() if e.kind == "experience")
    draft = {"summary": "Data Analytics student using SQL and Python.",
             "skills": [{"category": "Languages", "items": ["Python", "SQL"]}],
             "sections": [{"entry_id": exp.id, "bullets": [{"text": t} for t in list(exp.bullets.values())[:2]]}],
             "keywords": ["SQL", "Python", "Tableau"]}
    docx = tmp_path / "Alex_Taylor_Resume.docx"
    documents.write_resume(draft, P, docx)
    pdfs = documents.to_pdf([docx])
    if not pdfs:
        pytest.skip("Word not available for PDF export")
    r = ats_check.check(str(pdfs[0]), P.raw["contact"], draft["keywords"])
    labels = {label: ok for label, ok, _ in r["checks"]}
    assert labels["Name readable"] and labels["Email readable"] and labels["Phone readable"]
    assert labels["Fits on one page"] and labels["Sections read in the right order"]
    assert r["keyword_coverage"] == 67 and r["keywords_missing"] == ["Tableau"]


def test_stats_count_interviews_even_after_rejection(tmpdb):
    with db.session() as c:
        add_job(c, 1, "Data Analyst Intern", "RBC", "applied", 3)
        add_job(c, 2, "Software Developer Co-op", "Tiny Startup", "applied", 3, source="linkedin")
        db.record_status(c, 2, "interview")
        db.record_status(c, 2, "rejected")                  # interview, then rejected: still an interview
        c.execute("UPDATE jobs SET status='rejected' WHERE id=2")
        add_job(c, 3, "ML Intern", "Shopify", "new")        # never applied: not counted
    s = stats.compute()
    assert s["applied"] == 2 and s["interviews"] == 1 and s["rate"] == 50
    roles = {r["group"]: r for r in s["groups"]["Role type"]}
    assert roles["Software / engineering"]["interviews"] == 1
    src = {r["group"]: r for r in s["groups"]["Where JobApp found it"]}
    assert src["Found on company site"]["applied"] == 1


def test_followups_due_snooze_and_done(tmpdb):
    with db.session() as c:
        add_job(c, 1, "Data Analyst Intern", "RBC", "applied", 9)     # due
        add_job(c, 2, "Data Analyst Intern", "TD", "applied", 2)      # not yet
        add_job(c, 3, "Data Analyst Intern", "BMO", "interview", 20)  # heard back: no reminder
    f = followups.lists()
    assert [j["id"] for j in f["due"]] == [1] and [j["id"] for j in f["upcoming"]] == [2]
    followups.snooze(1)
    assert followups.lists()["due"] == []
    with db.session() as c:
        c.execute("UPDATE jobs SET followup_snoozed_until=NULL WHERE id=1")
    followups.mark_done(1)
    f = followups.lists()
    assert f["due"] == [] and [j["id"] for j in f["done"]] == [1]


def test_mark_sent_records_followup(tmpdb):
    with db.session() as c:
        add_job(c, 1, "Data Analyst Intern", "RBC", "applied", 9)
        oid = c.execute("INSERT INTO outreach (job_id, kind, status, data, created_at) "
                        "VALUES (1, 'followup', 'ready', '{}', datetime('now'))").lastrowid
    outreach.mark_sent(oid)
    assert followups.lists()["done"][0]["id"] == 1


def test_linkedin_searches():
    s = outreach.linkedin_searches("CIBC", "Data Analyst co-op (Winter 2027)")
    assert len(s) == 3 and all(x["url"].startswith("https://www.linkedin.com/search/results/people/") for x in s)
    assert "Example+University" in s[0]["url"] and "Data+Analyst" in s[2]["url"]


def test_pages_render(tmpdb):
    from fastapi.testclient import TestClient
    from app.main import app
    with db.session() as c:
        add_job(c, 1, "Data Analyst Intern", "RBC", "applied", 9)
    client = TestClient(app)
    for url, text in [("/stats", "What's working"), ("/followups", "Due now (1)"), ("/job/1", "Get a referral")]:
        r = client.get(url)
        assert r.status_code == 200 and text in r.text, url


def test_located_needs_says_where_each_placeholder_is():
    from app.drafting import located_needs, open_needs
    exp = next(e for e in P.entries.values() if e.kind == "experience")
    draft = {"summary": "Student.", "cover_letter": "Hi [NEEDS: first name],\nThanks.",
             "sections": [{"entry_id": exp.id, "bullets": [{"text": "Did X."}, {"text": "Cut time by [NEEDS: %]."}]}]}
    found = located_needs(draft, P.entries)
    assert [n["text"] for n in found] == ["[NEEDS: first name]", "[NEEDS: %]"]
    assert found[0]["where"] == "Cover letter" and found[0]["field"] == "cover_letter"
    assert found[1]["where"].endswith("bullet 2") and found[1]["field"] == f"sec_{exp.id}"
    assert len(found) == len(open_needs(draft))   # same count that blocks approval


def test_i_have_this_adds_keyword_to_profile_and_resume(tmpdb, tmp_path, monkeypatch):
    import app.profile as prof
    from app import drafting
    monkeypatch.setattr(prof, "added_skills_path", lambda: tmp_path / "added.local.yaml")
    exp = next(e for e in P.entries.values() if e.kind == "experience")
    draft = {"summary": "x", "cover_letter": "Dear Hiring Manager,", "keywords": ["Data Quality", "Python"],
             "skills": [{"category": "Languages", "items": ["Python"]}, {"category": "Data", "items": ["SQL"]}],
             "sections": [{"entry_id": exp.id, "bullets": [{"text": "Did X.", "source": [f"{exp.id}.b0"]}]}]}
    with db.session() as c:
        c.execute("INSERT INTO jobs (id, title, company, found_at) VALUES (1, 'Data Analyst', 'BMO', datetime('now'))")
        did = c.execute("INSERT INTO drafts (job_id, status, draft, created_at, updated_at) VALUES (1, 'ready', ?, 'x', 'x')",
                        (json.dumps(draft),)).lastrowid
    with pytest.raises(ValueError):           # must say where you used it
        drafting.add_keyword(did, "Data Quality", "")
    msg = drafting.add_keyword(did, "Data Quality", "Campus Datathon")
    assert "profile" in msg
    assert "data quality" in prof.load_profile().all_skills
    assert "Data Quality (used in: Campus Datathon)" in prof.load_profile().as_prompt()
    d = drafting.latest(1)
    assert "Data Quality" in d["draft"]["skills"][1]["items"]          # joined the "Data" line
    assert "Data Quality" in d["scores"]["ats"]["matched"]


def test_company_why_is_saved_per_company_and_reaches_the_prompt(tmpdb):
    from app import company
    from app.tailor import build_user
    company.set_why("BMO Financial Group", "I'm in the TechBiz club and want to work in fintech.")
    assert company.get_why("BMO") == company.get_why("Bank of Montreal") != ""
    prompt = build_user({"title": "Data Analyst", "company": "BMO", "description": "x"}, P)
    assert "TechBiz club" in prompt and "REASONS FOR WANTING TO WORK AT BMO" in prompt
    company.set_why("BMO", "")
    assert company.get_why("BMO") == ""


def test_draft_page_buttons_end_to_end(tmpdb, tmp_path, monkeypatch):
    """Click 'I have this', 'Add to resume' and 'Save' for Why-this-company through the real routes."""
    import app.profile as prof
    from fastapi.testclient import TestClient
    from app import company, drafting
    from app.main import app as web
    monkeypatch.setattr(prof, "added_skills_path", lambda: tmp_path / "added.local.yaml")
    exp = next(e for e in P.entries.values() if e.kind == "experience")
    draft = {"summary": "Student.", "cover_letter": "Dear Hiring Manager,", "keywords": ["Data Quality", "Spark SQL"],
             "skills": [{"category": "Data", "items": ["SQL"]}],
             "sections": [{"entry_id": exp.id, "bullets": [{"text": "Did X.", "source": [f"{exp.id}.b0"]}]}]}
    with db.session() as c:
        c.execute("INSERT INTO jobs (id, title, company, found_at) VALUES (7, 'Data Analyst', 'BMO', datetime('now'))")
        did = c.execute("INSERT INTO drafts (job_id, status, draft, created_at, updated_at) VALUES (7, 'ready', ?, 'x', 'x')",
                        (json.dumps(draft),)).lastrowid
    client = TestClient(web)
    form = {"job_id": "7", "summary": "Edited summary.", "cover_letter": "Dear Hiring Manager,", "skills": "Data: SQL",
            f"sec_{exp.id}": "Did X.", "company_why": ""}
    page = client.get("/job/7/draft").text
    assert "I have this" in page and "Add to resume" in page          # Data Quality gap, Spark SQL fixable
    r = client.post(f"/draft/{did}/keyword", data={**form, "add_keyword": "Data Quality", "where_Data Quality": "Datathon"})
    assert r.status_code == 200 and "Added Data Quality to your profile" in r.text
    form["skills"] = "Data: SQL, Data Quality"                         # what the reloaded page now shows
    r = client.post(f"/draft/{did}/keyword", data={**form, "add_keyword": "Spark SQL"})
    d = drafting.latest(7)["draft"]
    assert d["summary"] == "Edited summary."                            # unsaved edits were kept
    assert {"Data Quality", "Spark SQL"} <= set(d["skills"][0]["items"])
    client.post(f"/draft/{did}/company-why", data={**form, "company_why": "Fintech + TechBiz club."})
    assert company.get_why("BMO") == "Fintech + TechBiz club."
    assert "Fintech + TechBiz club." in client.get("/job/7").text
