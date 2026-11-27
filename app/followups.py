"""Which applications are due a follow-up, plus a once-a-day desktop reminder."""
import json
from datetime import date, datetime, timedelta, timezone

from . import db
from .config import DATA_DIR, load_config
from .notify import toast

STATE = DATA_DIR / "state.json"


def days_setting() -> int:
    return int((load_config().get("followups") or {}).get("days", 7))


def lists() -> dict:
    """due: applied >= N days ago, no follow-up yet, not snoozed. upcoming: applied, not due yet."""
    n = days_setting()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%d %H:%M:%S")
    with db.session() as c:
        base = ("SELECT id, title, company, applied_at, followed_up_at, followup_snoozed_until FROM jobs "
                "WHERE status='applied' AND applied_at IS NOT NULL ")
        due = c.execute(base + "AND followed_up_at IS NULL AND applied_at <= ? "
                        "AND (followup_snoozed_until IS NULL OR followup_snoozed_until <= ?) ORDER BY applied_at",
                        (cutoff, now)).fetchall()
        upcoming = c.execute(base + "AND followed_up_at IS NULL AND (applied_at > ? OR followup_snoozed_until > ?) "
                             "ORDER BY applied_at", (cutoff, now)).fetchall()
        done = c.execute(base + "AND followed_up_at IS NOT NULL ORDER BY followed_up_at DESC LIMIT 30").fetchall()
    return {"days": n, "due": [dict(r) for r in due], "upcoming": [dict(r) for r in upcoming],
            "done": [dict(r) for r in done]}


def snooze(job_id: int, days: int = 3):
    until = (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    with db.session() as c:
        c.execute("UPDATE jobs SET followup_snoozed_until=? WHERE id=?", (until, job_id))


def mark_done(job_id: int):
    with db.session() as c:
        c.execute("UPDATE jobs SET followed_up_at=COALESCE(followed_up_at, datetime('now')) WHERE id=?", (job_id,))


def daily_reminder():
    """Called hourly by the scheduler; pops at most one reminder per day."""
    if not (load_config().get("followups") or {}).get("toast", True):
        return
    try:
        state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    except ValueError:
        state = {}
    today = date.today().isoformat()
    if state.get("followup_toast") == today or datetime.now().hour < 9:
        return
    due = lists()["due"]
    if due:
        names = ", ".join(j["company"] for j in due[:3])
        toast(f"{len(due)} follow-up(s) due", f"{names}{'…' if len(due) > 3 else ''}. Open JobApp → Follow-ups.",
              "http://localhost:8000/followups")
        state["followup_toast"] = today
        STATE.write_text(json.dumps(state), encoding="utf-8")
