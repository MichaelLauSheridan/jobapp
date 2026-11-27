"""Sync jobs to the Google Sheet tracker through the Apps Script web app (app/tracker_script.gs).

Every change goes into the `tracker_outbox` table first, then gets sent. If sending fails (offline,
Google hiccup), it's retried every few minutes, so nothing is lost and the dashboard never waits.
"""
import json
import logging
import os
import re
import secrets
import threading
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv, set_key

from . import db
from .config import ROOT, load_config

log = logging.getLogger(__name__)
ENV = ROOT / ".env"
_send_lock = threading.Lock()

OUTBOX = """
CREATE TABLE IF NOT EXISTS tracker_outbox (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    attempts INTEGER DEFAULT 0,
    last_error TEXT,
    sent_at TEXT,
    result TEXT
);
CREATE INDEX IF NOT EXISTS idx_outbox_pending ON tracker_outbox(sent_at);
-- The key (Job Posting URL) a job was first written with, so its row is always found again
-- even if a better link for the same job turns up later.
CREATE TABLE IF NOT EXISTS tracker_keys (job_id INTEGER PRIMARY KEY, key TEXT NOT NULL, link_source TEXT);
"""

COMPANY_SITES = ("greenhouse", "lever", "ashby", "workday")
_PERIOD = re.compile(r"\b(\d{1,2})(?:\s*(?:-|–|to|or)\s*(\d{1,2}))?[\s-]*months?\b", re.I)


def period_months(text: str) -> str:
    """'8-month co-op' -> '8'; '12–16 months' -> '12 - 16'; nothing found -> ''."""
    nums = set()
    for a, b in _PERIOD.findall(text or ""):
        for x in (a, b):
            if x and 3 <= int(x) <= 24:
                nums.add(int(x))
    if not nums:
        return ""
    lo, hi = min(nums), max(nums)
    return str(lo) if lo == hi else f"{lo} - {hi}"


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def cfg() -> dict:
    return load_config().get("tracker", {}) or {}


def credentials() -> tuple[str, str]:
    """(web app URL, secret). Creates a secret in .env on first use."""
    load_dotenv(ENV, override=True)
    secret = os.environ.get("TRACKER_SECRET", "")
    if not secret:
        secret = secrets.token_urlsafe(24)
        ENV.touch(exist_ok=True)
        set_key(str(ENV), "TRACKER_SECRET", secret)
        os.environ["TRACKER_SECRET"] = secret
    return os.environ.get("TRACKER_URL", "").strip(), secret


def save_url(url: str):
    ENV.touch(exist_ok=True)
    set_key(str(ENV), "TRACKER_URL", url.strip())
    os.environ["TRACKER_URL"] = url.strip()


def script_text() -> str:
    _, secret = credentials()
    src = (ROOT / "app" / "tracker_script.gs").read_text(encoding="utf-8")
    return src.replace("__SECRET__", secret).replace("__HEADER_ROW__", str(cfg().get("header_row", 1)))


def enabled() -> bool:
    url, _ = credentials()
    return bool(cfg().get("enabled") and url)


# ---------------- building rows ----------------
def _local_date(ts: str | None) -> str:
    """Stored UTC timestamp -> local 'YYYY-MM-DD' (the Apps Script turns it into a real date)."""
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00").replace(" ", "T"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%Y-%m-%d")
    except ValueError:
        return str(ts)[:10]


def row_values(job: dict, link: str, link_source: str, files: list[str] | None) -> dict:
    """Map our fields to the sheet's headers using config.yaml -> tracker.columns."""
    t = cfg()
    fields = {
        "company": job["company"],
        "role": job["title"].strip(),
        "job_type": t.get("job_type", ""),
        "period": period_months(f"{job['title']}\n{job.get('description') or ''}"),
        "location": (job.get("location") or "") + (" (Remote)" if job.get("is_remote") else ""),
        "link": link,
        "portal": (t.get("portal_map") or {}).get(link_source, ""),
        "status": (t.get("status_map") or {}).get(job["status"], ""),
        "date_found": _local_date(job.get("found_at")),
        "date_applied": _local_date(job.get("applied_at")),
        "score": job.get("score"),
        "notes": job.get("notes") or "",
        "resume_file": next((os.path.basename(f) for f in files or [] if "Resume" in f and f.endswith(".pdf")), ""),
    }
    return {header: fields[f] for f, header in (t.get("columns") or {}).items() if header and f in fields}


def enqueue(job_id: int, reason: str = ""):
    """Queue a sync for this job if its status is one we track. Safe to call from anywhere."""
    t = cfg()
    if not t.get("enabled"):
        return
    with db.session() as c:
        c.executescript(OUTBOX)
        job = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job or job["status"] not in t.get("sync_statuses", []):
            return
        job = dict(job)
        saved = c.execute("SELECT key, link_source FROM tracker_keys WHERE job_id=?", (job_id,)).fetchone()
        if saved:
            link, source = saved["key"], saved["link_source"] or ""
        else:
            links = c.execute("SELECT source, url FROM job_links WHERE job_id=? ORDER BY first_seen",
                              (job_id,)).fetchall()
            # Prefer the company's own posting over LinkedIn/Indeed copies.
            best = sorted(links, key=lambda l: l["source"] not in COMPANY_SITES)
            link, source = (best[0]["url"], best[0]["source"]) if best else (f"jobapp-{job_id}", "")
            c.execute("INSERT INTO tracker_keys (job_id, key, link_source) VALUES (?,?,?)", (job_id, link, source))
        d = c.execute("SELECT files FROM drafts WHERE job_id=? AND status='approved' ORDER BY id DESC LIMIT 1",
                      (job_id,)).fetchone()
        files = json.loads(d["files"]) if d and d["files"] else None
        key_header = t.get("key_column", "Job Posting URL")
        payload = {"sheet": t.get("sheet_name") or "", "key_header": key_header, "key": link,
                   "anchor_header": t.get("anchor_column", ""), "date_headers": t.get("date_columns", []),
                   "add_key_column": bool(t.get("add_key_column", False)),
                   "values": row_values(job, link, source, files)}
        # Collapse: only the newest pending payload per job matters.
        c.execute("DELETE FROM tracker_outbox WHERE job_id=? AND sent_at IS NULL", (job_id,))
        c.execute("INSERT INTO tracker_outbox (job_id, payload, created_at) VALUES (?,?,?)",
                  (job_id, json.dumps(payload), _now()))
    threading.Thread(target=flush, daemon=True).start()


def _post(url: str, body: dict) -> dict:
    with httpx.Client(timeout=30, follow_redirects=True) as c:
        r = c.post(url, json=body)
    r.raise_for_status()
    try:
        return r.json()
    except ValueError:
        raise RuntimeError("Apps Script didn't return JSON. Is the deployment set to 'Anyone' access?")


def flush() -> dict:
    """Send all pending outbox rows. Called after each enqueue and by the scheduler."""
    if not enabled():
        return {"sent": 0, "skipped": "tracker not configured"}
    url, secret = credentials()
    sent = failed = 0
    with _send_lock:
        with db.session() as c:
            c.executescript(OUTBOX)
            pending = c.execute("SELECT * FROM tracker_outbox WHERE sent_at IS NULL AND attempts < 20 "
                                "ORDER BY id").fetchall()
        for row in pending:
            body = json.loads(row["payload"]) | {"secret": secret}
            try:
                res = _post(url, body)
                if not res.get("ok"):
                    raise RuntimeError(res.get("error", "unknown error"))
                with db.session() as c:
                    c.execute("UPDATE tracker_outbox SET sent_at=?, result=?, last_error=NULL, attempts=attempts+1 "
                              "WHERE id=?", (_now(), json.dumps(res), row["id"]))
                if res.get("missing_headers"):
                    log.warning("Tracker sheet has no column(s): %s", res["missing_headers"])
                sent += 1
            except Exception as e:
                with db.session() as c:
                    c.execute("UPDATE tracker_outbox SET attempts=attempts+1, last_error=? WHERE id=?",
                              (str(e)[:300], row["id"]))
                failed += 1
    return {"sent": sent, "failed": failed}


def test_connection(url: str | None = None) -> dict:
    url = (url or credentials()[0]).strip()
    if not url:
        return {"ok": False, "error": "No web app URL saved yet."}
    if not url.startswith("https://script.google.com/"):
        return {"ok": False, "error": "That doesn't look like an Apps Script web app URL (https://script.google.com/...)."}
    _, secret = credentials()
    try:
        with httpx.Client(timeout=30, follow_redirects=True) as c:
            r = c.get(url, params={"secret": secret, "sheet": cfg().get("sheet_name") or ""})
        return r.json()
    except ValueError:
        return {"ok": False, "error": "Got a web page instead of JSON. Check the deployment's access is 'Anyone'."}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def job_sync_state(job_id: int) -> dict | None:
    with db.session() as c:
        c.executescript(OUTBOX)
        r = c.execute("SELECT * FROM tracker_outbox WHERE job_id=? ORDER BY id DESC LIMIT 1", (job_id,)).fetchone()
    return dict(r) if r else None


def check_mapping(headers: list[str]) -> dict:
    cols = cfg().get("columns") or {}
    hset = {h.strip() for h in headers if h}
    return {"mapped": {f: h for f, h in cols.items() if h in hset},
            "missing": {f: h for f, h in cols.items() if h and h not in hset},
            "unused_headers": [h for h in headers if h and h not in cols.values()]}
