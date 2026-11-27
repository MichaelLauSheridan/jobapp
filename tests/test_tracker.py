import json

import pytest

from app import db, tracker


@pytest.fixture
def fake_sheet(tmp_path, monkeypatch):
    """Temporary DB + a fake Apps Script that records what would be written."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    import app.db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    monkeypatch.setattr(tracker, "credentials", lambda: ("https://script.google.com/macros/s/x/exec", "s3cret"))
    real_cfg = tracker.cfg
    monkeypatch.setattr(tracker, "cfg", lambda: {**real_cfg(), "enabled": True})  # tests run even while paused
    monkeypatch.setattr(tracker, "threading", type("T", (), {"Thread": lambda *a, **k: type("N", (), {"start": lambda s: None})()}))
    calls = []

    def fake_post(url, body):
        calls.append(body)
        return {"ok": True, "action": "added", "row": 2, "missing_headers": []}

    monkeypatch.setattr(tracker, "_post", fake_post)
    with db.session() as c:
        c.execute("INSERT INTO jobs (id, title, company, location, found_at, status, applied_at) VALUES "
                  "(1, 'Data Analyst co-op', 'CIBC', 'Toronto, ON', '2026-11-20 14:00:00', 'new', NULL)")
        c.execute("INSERT INTO job_links (job_id, source, external_id, url, first_seen, last_seen) VALUES "
                  "(1, 'linkedin', 'a', 'https://linkedin.com/x', '2026-11-20', '2026-11-20'),"
                  "(1, 'workday', 'b', 'https://cibc.wd3.myworkdayjobs.com/x', '2026-11-21', '2026-11-21')")
    return calls


def set_status(status):
    with db.session() as c:
        c.execute("UPDATE jobs SET status=?, applied_at=CASE WHEN ?='applied' THEN '2026-11-24 15:00:00' "
                  "ELSE applied_at END WHERE id=1", (status, status))


def test_untracked_status_not_synced(fake_sheet):
    tracker.enqueue(1)            # status 'new' isn't in sync_statuses
    assert tracker.flush()["sent"] == 0 and fake_sheet == []


def test_applied_job_matches_tracker_sheet_columns(fake_sheet):
    set_status("applied")
    tracker.enqueue(1)
    assert tracker.flush() == {"sent": 1, "failed": 0}
    body = fake_sheet[0]
    v = body["values"]
    assert body["secret"] == "s3cret"
    assert "myworkdayjobs" in body["key"]                    # keyed by URL, company site preferred
    assert body["key_header"] == "Job Posting URL" and body["anchor_header"] == "Position"
    assert v["Company Name"] == "CIBC" and v["Position"] == "Data Analyst co-op"
    assert v["Status of Application"] == "Under Consideration"   # exact dropdown value
    assert v["Applied Portal"] == "Company Website"
    assert v["Job Type"] == "Internship"
    assert v["Date Applied"] == "2026-11-24" and "Date Applied" in body["date_headers"]


def test_key_stays_the_same_when_a_better_link_appears(fake_sheet):
    with db.session() as c:
        c.execute("DELETE FROM job_links WHERE source='workday'")
    set_status("applied"); tracker.enqueue(1); tracker.flush()
    first = fake_sheet[-1]["key"]
    with db.session() as c:
        c.execute("INSERT INTO job_links (job_id, source, external_id, url, first_seen, last_seen) VALUES "
                  "(1, 'workday', 'c', 'https://cibc.wd3.myworkdayjobs.com/y', '2026-11-25', '2026-11-25')")
    set_status("interview"); tracker.enqueue(1); tracker.flush()
    assert fake_sheet[-1]["key"] == first and "linkedin" in first
    assert fake_sheet[-1]["values"]["Status of Application"] == "Interview Completed"


def test_period_months():
    assert tracker.period_months("Data Analyst Co-op (8 Month - Hybrid)") == "8"
    assert tracker.period_months("12–16 months starting May") == "12 - 16"
    assert tracker.period_months("4-month or 8-month work term") == "4 - 8"
    assert tracker.period_months("Summer Student") == ""


def test_pending_updates_collapse_to_latest(fake_sheet):
    set_status("applied"); tracker.enqueue(1)
    set_status("rejected"); tracker.enqueue(1)
    tracker.flush()
    assert len(fake_sheet) == 1 and fake_sheet[0]["values"]["Status of Application"] == "Rejected"


def test_failures_are_retried(fake_sheet, monkeypatch):
    set_status("applied"); tracker.enqueue(1)
    monkeypatch.setattr(tracker, "_post", lambda u, b: (_ for _ in ()).throw(RuntimeError("offline")))
    assert tracker.flush() == {"sent": 0, "failed": 1}
    monkeypatch.setattr(tracker, "_post", lambda u, b: {"ok": True})
    assert tracker.flush() == {"sent": 1, "failed": 0}


def test_mapping_check():
    m = tracker.check_mapping(["No.", "Position", "Company Name", "Contact Name"])
    assert "company" in m["mapped"] and "Contact Name" in m["unused_headers"]


def test_script_has_secret_filled():
    s = tracker.script_text()
    assert "__SECRET__" not in s and "__HEADER_ROW__" not in s
