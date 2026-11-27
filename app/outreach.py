"""Draft follow-up emails and referral (LinkedIn) messages. JobApp NEVER sends anything:
you copy the text and send it yourself, then click "Mark sent"."""
import json
import logging
import threading
from datetime import datetime, timezone
from urllib.parse import quote_plus

from . import db, humanize
from .profile import load_profile
from .tailor import TailorError, call_model

log = logging.getLogger(__name__)
LINKEDIN_NOTE_LIMIT = 300

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["subject", "message", "short_note", "needs"],
    "properties": {
        "subject": {"type": "string", "description": "Email subject (follow-up only, else empty)"},
        "message": {"type": "string"},
        "short_note": {"type": "string", "description": "LinkedIn connection note under 300 characters (referral only, else empty)"},
        "needs": {"type": "array", "items": {"type": "string"}},
    },
}

COMMON = """You write short outreach messages for a student applying to internships.

ABSOLUTE RULES
- Use only facts from the CANDIDATE PROFILE and the JOB. Never invent names, titles, shared history,
  conversations, metrics or company facts. Never claim to know the reader.
- If a name or detail would really help and isn't given, use [NEEDS: …] (for a greeting with no name,
  use "Hi [NEEDS: first name]," so the candidate fills it in).
- Plain text, no markdown, no emojis, no hashtags. Use absolute dates.
- Follow the HUMANIZE RULES below in draft mode: direct, warm, no stock openers ("I hope this email
  finds you well", "I wanted to reach out"), no "passionate", no em dashes.
"""

FOLLOWUP = COMMON + """
TASK: a follow-up email to the recruiter or hiring team about an application already submitted.
- "subject" says the point, e.g. "Following up: Data Analyst Co-op application (applied 24 Nov)".
- "message": under 120 words. First two sentences: which role, when applied, and that you're still
  very interested. Then ONE concrete reason you fit, taken from the profile and tied to the posting.
  End with a light ask (any update on timing, happy to share anything else). Sign off "Best," then the candidate's full name from the profile.
- "short_note" must be "".
"""

REFERRAL = COMMON + """
TASK: messages to a person at the company the candidate found on LinkedIn (an alum, a recruiter or
someone on the team). Goal: a short chat or advice, which can lead to a referral. Don't ask for a
referral in the first note.
- "short_note": a LinkedIn connection request UNDER 280 characters. Name the role or team, one real
  hook from the profile, and a small ask (a quick question or 15-minute chat). Mention the candidate's school
  only as "I'm a <program> student at <school>"; never claim the reader went there.
- "message": the follow-up after they accept, under 110 words: thanks, the specific role (with job
  title), one concrete fit point, 2 specific questions about the team or role, and an offer to send
  the resume. Sign off with the candidate's first name.
- "subject" must be "".
"""


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def linkedin_searches(company: str, title: str) -> list[dict]:
    """People-search links you open yourself (JobApp doesn't browse LinkedIn)."""
    words = [w for w in title.replace(",", " ").split()
             if w.lower() not in {"intern", "internship", "co-op", "coop", "student", "summer", "winter", "fall",
                                  "2026", "2027", "-", "–", "(winter", "(summer", "term", "&"}]
    team = " ".join(words[:3])
    school = load_profile().raw.get("application", {}).get("school", "")
    base = "https://www.linkedin.com/search/results/people/?keywords="
    return [
        *([{"label": f"{school} alumni at {company}", "url": base + quote_plus(f'"{company}" "{school}"')}]
          if school else []),
        {"label": f"Campus / university recruiters at {company}", "url": base + quote_plus(f'"{company}" campus recruiter')},
        {"label": f"People working in {team or 'this area'} at {company}", "url": base + quote_plus(f'"{company}" {team}')},
    ]


def _user_prompt(job: dict, kind: str) -> str:
    p = load_profile()
    with db.session() as c:
        d = c.execute("SELECT draft FROM drafts WHERE job_id=? AND status IN ('approved','ready') ORDER BY id DESC LIMIT 1",
                      (job["id"],)).fetchone()
    tailored = ""
    if d and d["draft"]:
        dr = json.loads(d["draft"])
        tailored = f"\nTAILORED SUMMARY USED FOR THIS JOB: {dr.get('summary', '')}\n"
    applied = (job.get("applied_at") or "")[:10]
    desc = (job.get("description") or "")[:3000]
    return (f"CANDIDATE PROFILE\n{p.as_prompt()}\n{tailored}\n"
            f"JOB\nRole: {job['title']}\nCompany: {job['company']}\nLocation: {job.get('location') or ''}\n"
            f"Applied on: {applied or 'not yet'}\n\n{desc}\n\nWrite the {kind} messages now.")


def start(job_id: int, kind: str) -> int:
    assert kind in ("followup", "referral")
    with db.session() as c:
        job = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            raise ValueError("job not found")
        oid = c.execute("INSERT INTO outreach (job_id, kind, status, created_at) VALUES (?,?, 'running', ?)",
                        (job_id, kind, _now())).lastrowid
    threading.Thread(target=_run, args=(oid, dict(job), kind), daemon=True).start()
    return oid


def _run(oid: int, job: dict, kind: str):
    try:
        system = (FOLLOWUP if kind == "followup" else REFERRAL) + "\nHUMANIZE RULES\n" + humanize.rules_text()
        out, meta = call_model(system, _user_prompt(job, kind), SCHEMA, max_effort="low")
        out["check"] = humanize.check(out.get("message", ""), "letter")
        note = out.get("short_note", "")
        out["note_chars"] = len(note)
        out["note_too_long"] = len(note) > LINKEDIN_NOTE_LIMIT
        out["meta"] = meta
        with db.session() as c:
            c.execute("UPDATE outreach SET status='ready', data=? WHERE id=?", (json.dumps(out), oid))
    except Exception as e:
        msg = str(e) if isinstance(e, TailorError) else f"{type(e).__name__}: {e}"
        log.warning("Outreach draft failed: %s", msg)
        with db.session() as c:
            c.execute("UPDATE outreach SET status='error', error=? WHERE id=?", (msg[:500], oid))


def latest(job_id: int) -> dict:
    """{'followup': row|None, 'referral': row|None}"""
    out = {}
    with db.session() as c:
        for kind in ("followup", "referral"):
            r = c.execute("SELECT * FROM outreach WHERE job_id=? AND kind=? ORDER BY id DESC LIMIT 1",
                          (job_id, kind)).fetchone()
            if r:
                r = dict(r)
                r["data"] = json.loads(r["data"]) if r["data"] else None
            out[kind] = r
    return out


def mark_sent(oid: int):
    with db.session() as c:
        r = c.execute("SELECT job_id, kind FROM outreach WHERE id=?", (oid,)).fetchone()
        c.execute("UPDATE outreach SET status='sent', sent_at=? WHERE id=?", (_now(), oid))
        if r and r["kind"] == "followup":
            c.execute("UPDATE jobs SET followed_up_at=COALESCE(followed_up_at, ?) WHERE id=?", (_now(), r["job_id"]))
        return r["job_id"] if r else None
