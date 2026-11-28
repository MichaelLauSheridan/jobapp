"""Small summaries for the dashboard: the greeting, progress toward the application goal, and
"nudges" (things waiting on you). Everything here is a quick read-only database query."""
import math
import time
from datetime import datetime

from . import db, followups
from .profile import load_profile

APPLICATION_GOAL = 200
APPLIED_STATUSES = ("applied", "interview", "offer", "rejected")

_nav_cache = {"at": 0.0, "value": {}}


def nav_counts() -> dict:
    """Badge numbers for the top menu. Cached for 30 seconds so every page load stays fast."""
    if time.time() - _nav_cache["at"] > 30:
        _nav_cache["value"] = {"followups_due": len(followups.lists()["due"])}
        _nav_cache["at"] = time.time()
    return _nav_cache["value"]


def _greeting() -> str:
    hour = datetime.now().hour
    part = "Morning" if hour < 12 else "Afternoon" if hour < 18 else "Evening"
    name = (load_profile().raw.get("contact", {}).get("name") or "").split(" ")[0]
    return f"{part}, {name}" if name else part


def summary() -> dict:
    with db.session() as c:
        fresh = c.execute("SELECT COUNT(*) FROM jobs WHERE found_at >= datetime('now', '-1 day')"
                          " AND status IN ('new','saved','ready')").fetchone()[0]
        applied = c.execute(f"SELECT COUNT(*) FROM jobs WHERE status IN {APPLIED_STATUSES}").fetchone()[0]
        docs_ready = c.execute("SELECT id, company, title FROM jobs WHERE status='ready'"
                               " ORDER BY score DESC LIMIT 3").fetchall()
        drafts_with_gaps = c.execute(
            "SELECT j.id, j.company FROM drafts d JOIN jobs j ON j.id = d.job_id"
            " WHERE d.status = 'ready' AND d.draft LIKE '%[NEEDS:%'"
            " AND d.id = (SELECT MAX(id) FROM drafts WHERE job_id = d.job_id)"
            " ORDER BY d.id DESC LIMIT 3").fetchall()
        notes_ready = c.execute(
            "SELECT j.id, j.company FROM outreach o JOIN jobs j ON j.id = o.job_id"
            " WHERE o.kind = 'referral' AND o.status = 'ready' ORDER BY o.id DESC LIMIT 3").fetchall()

    nudges = []   # (pastel colour, text, link)
    for j in followups.lists()["due"][:3]:
        nudges.append(("bg-amber-100", f"Follow up with {j['company']}", f"/job/{j['id']}#outreach"))
    for j in docs_ready:
        nudges.append(("bg-green-100", f"{j['company']}: documents ready, time to apply", f"/job/{j['id']}/draft"))
    for j in drafts_with_gaps:
        nudges.append(("bg-sky-100", f"{j['company']} draft needs a few details", f"/job/{j['id']}/draft"))
    for j in notes_ready:
        nudges.append(("bg-violet-100", f"{j['company']} referral note is ready to send", f"/job/{j['id']}#outreach"))

    remaining = max(APPLICATION_GOAL - applied, 0)
    return {
        "greeting": _greeting(),
        "fresh": fresh,
        "applied": applied,
        "goal": APPLICATION_GOAL,
        "progress": min(100, round(100 * applied / APPLICATION_GOAL)),
        "remaining": remaining,
        "days_at_five": math.ceil(remaining / 5),
        "nudges": nudges,
    }
