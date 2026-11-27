"""Draft lifecycle: start tailoring in the background, apply your edits, approve into files."""
import json
import logging
import re
import threading
from datetime import datetime, timezone

from . import db
from .config import load_config
from .documents import write_all
from .profile import load_profile
from .tailor import TailorError, score, tailor

log = logging.getLogger(__name__)
NEEDS = re.compile(r"\[NEEDS:[^\]]*\]")


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def latest(job_id: int) -> dict | None:
    with db.session() as c:
        r = c.execute("SELECT * FROM drafts WHERE job_id=? ORDER BY id DESC LIMIT 1", (job_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    for k in ("draft", "scores", "meta", "files", "ats"):
        d[k] = json.loads(d[k]) if d[k] else None
    return d


def start(job_id: int) -> int:
    with db.session() as c:
        job = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            raise ValueError("job not found")
        running = c.execute("SELECT id FROM drafts WHERE job_id=? AND status='running'", (job_id,)).fetchone()
        if running:
            return running["id"]
        did = c.execute("INSERT INTO drafts (job_id, status, created_at) VALUES (?, 'running', ?)",
                        (job_id, _now())).lastrowid
    threading.Thread(target=_run, args=(did, dict(job)), daemon=True).start()
    return did


def _run(did: int, job: dict):
    try:
        r = tailor(job)
        with db.session() as c:
            c.execute("UPDATE drafts SET status='ready', draft=?, scores=?, meta=?, updated_at=? WHERE id=?",
                      (json.dumps(r["draft"]), json.dumps(r["scores"]), json.dumps(r["meta"]), _now(), did))
    except Exception as e:
        msg = str(e) if isinstance(e, TailorError) else f"{type(e).__name__}: {e}"
        log.exception("Tailoring failed") if not isinstance(e, TailorError) else log.warning(msg)
        with db.session() as c:
            c.execute("UPDATE drafts SET status='error', error=?, updated_at=? WHERE id=?", (msg[:1000], _now(), did))


def apply_edits(did: int, form: dict) -> dict:
    """form: summary, skills ("Category: a, b" per line), sec_<entry_id> (one bullet per line), cover_letter."""
    with db.session() as c:
        row = c.execute("SELECT * FROM drafts WHERE id=?", (did,)).fetchone()
    draft = json.loads(row["draft"])
    draft["summary"] = form.get("summary", draft["summary"]).strip()
    draft["cover_letter"] = form.get("cover_letter", draft["cover_letter"]).replace("\r\n", "\n").strip()
    if "skills" in form:
        skills = []
        for line in form["skills"].splitlines():
            if ":" in line:
                cat, items = line.split(":", 1)
                items = [i.strip() for i in items.split(",") if i.strip()]
                if items:
                    skills.append({"category": cat.strip(), "items": items})
        draft["skills"] = skills
    for sec in draft["sections"]:
        key = f"sec_{sec['entry_id']}"
        if key not in form:
            continue
        lines = [l.strip().lstrip("•*- ").strip() for l in form[key].splitlines() if l.strip()]
        old = sec["bullets"]
        sec["bullets"] = [
            {"text": t,
             "source": old[i]["source"] if i < len(old) else ["you"],
             "unconfirmed": old[i]["unconfirmed"] if i < len(old) else False}
            for i, t in enumerate(lines)]
    scores = score(draft, load_profile())
    with db.session() as c:
        c.execute("UPDATE drafts SET draft=?, scores=?, updated_at=?, status='ready' WHERE id=?",
                  (json.dumps(draft), json.dumps(scores), _now(), did))
    return draft


def open_needs(draft: dict) -> list[str]:
    texts = [draft.get("summary", ""), draft.get("cover_letter", "")]
    texts += [b["text"] for s in draft.get("sections", []) for b in s["bullets"]]
    return [m for t in texts for m in NEEDS.findall(t)]


def located_needs(draft: dict, entries: dict) -> list[dict]:
    """Every [NEEDS: …] placeholder still in the text, with where it is.

    Each item has:
      where: a readable label, e.g. "Cover letter" or "Data Analyst Intern | Example Nonprofit, bullet 2"
      field: the name of the text box on the draft page that holds it (used to jump there)
      text:  the placeholder itself
    """
    places = [("Cover letter", "cover_letter", draft.get("cover_letter", "")),
              ("Summary", "summary", draft.get("summary", ""))]
    for section in draft.get("sections", []):
        entry = entries.get(section["entry_id"])
        heading = entry.heading if entry else section["entry_id"]
        for number, bullet in enumerate(section["bullets"], start=1):
            places.append((f"{heading}, bullet {number}", f"sec_{section['entry_id']}", bullet["text"]))

    found = []
    for where, field, text in places:
        for placeholder in NEEDS.findall(text):
            found.append({"where": where, "field": field, "text": placeholder})
    return found


def approve(did: int) -> dict:
    with db.session() as c:
        row = c.execute("SELECT * FROM drafts WHERE id=?", (did,)).fetchone()
        job = dict(c.execute("SELECT * FROM jobs WHERE id=?", (row["job_id"],)).fetchone())
    draft = json.loads(row["draft"])
    left = open_needs(draft)
    if left:
        raise ValueError(f"{len(left)} [NEEDS: …] placeholder(s) still in the text. Fill them in or delete them first.")
    profile = load_profile()
    out = write_all(job, draft, profile, pdf=load_config().get("tailor", {}).get("export_pdf", True))
    from . import ats_check
    pdf = next((f for f in out["files"] if f.endswith("Resume.pdf")), None)
    try:
        ats = ats_check.check(pdf, profile.raw["contact"], draft.get("keywords", []))
    except Exception as e:
        ats = {"ok": False, "checks": [("ATS read-back", False, str(e)[:200])], "keyword_coverage": 0}
    with db.session() as c:
        c.execute("UPDATE drafts SET status='approved', approved_at=?, out_dir=?, files=?, ats=? WHERE id=?",
                  (_now(), out["dir"], json.dumps(out["files"]), json.dumps(ats), did))
        c.execute("UPDATE jobs SET status='ready' WHERE id=? AND status IN ('new','saved')", (row["job_id"],))
        st = c.execute("SELECT status FROM jobs WHERE id=?", (row["job_id"],)).fetchone()[0]
        db.record_status(c, row["job_id"], st)
    from . import tracker
    tracker.enqueue(row["job_id"])
    return out
