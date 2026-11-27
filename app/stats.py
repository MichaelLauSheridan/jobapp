"""'What's working': interview rates by role type, company type, source and resume type.

Counts a job as *applied* once it ever reached applied/interview/offer/rejected, and as an
*interview* once it ever reached interview or offer (from status_history, so a later rejection
after an interview still counts as an interview)."""
import re
from collections import defaultdict

from . import db
from .config import load_config
from .scoring import _is_preferred

APPLIED = ("applied", "interview", "offer", "rejected")
ROLE_TYPES = [("Data / analytics", r"\bdata\b|analyt|insight|business intelligence|\bbi\b"),
              ("Machine learning / AI", r"machine learning|\bml\b|\bai\b|artificial intelligence|scien"),
              ("Software / engineering", r"software|developer|engineer|programm|full.?stack|backend|frontend"),
              ("Business / other analyst", r"analyst")]
COMPANY_SITES = ("greenhouse", "lever", "ashby", "workday")


def role_type(title: str) -> str:
    t = title.lower()
    return next((name for name, pat in ROLE_TYPES if re.search(pat, t)), "Other")


def compute() -> dict:
    cfg = load_config()
    with db.session() as c:
        rows = c.execute("""
            SELECT j.id, j.title, j.company, j.score, j.status, j.applied_at, j.followed_up_at,
                   (SELECT GROUP_CONCAT(DISTINCT h.status) FROM status_history h WHERE h.job_id=j.id) AS hist,
                   (SELECT GROUP_CONCAT(DISTINCT l.source) FROM job_links l WHERE l.job_id=j.id) AS sources,
                   (SELECT COUNT(*) FROM drafts d WHERE d.job_id=j.id AND d.status='approved') AS tailored
            FROM jobs j""").fetchall()
    jobs = []
    for r in rows:
        hist = set((r["hist"] or "").split(",")) | {r["status"]}
        if not hist & set(APPLIED):
            continue
        sources = set((r["sources"] or "").split(","))
        jobs.append({
            "interview": bool(hist & {"interview", "offer"}), "offer": "offer" in hist,
            "role": role_type(r["title"]),
            "company": "Well-known employer" if _is_preferred(r["company"], cfg) else "Other employers",
            "source": "Found on company site" if sources & set(COMPANY_SITES) else "Found on LinkedIn/Indeed only",
            "resume": "Tailored by JobApp" if r["tailored"] else "Not tailored",
            "fit": "Fit score 70+" if (r["score"] or 0) >= 70 else ("Fit score 50-69" if (r["score"] or 0) >= 50 else "Fit score <50"),
            "followed_up": "Followed up" if r["followed_up_at"] else "No follow-up",
        })

    def breakdown(field):
        g = defaultdict(lambda: [0, 0])
        for j in jobs:
            g[j[field]][0] += 1
            g[j[field]][1] += j["interview"]
        return sorted(({"group": k, "applied": a, "interviews": i, "rate": round(100 * i / a) if a else 0}
                       for k, (a, i) in g.items()), key=lambda x: (-x["applied"]))

    n = len(jobs)
    iv = sum(j["interview"] for j in jobs)
    return {
        "applied": n, "interviews": iv, "offers": sum(j["offer"] for j in jobs),
        "rate": round(100 * iv / n) if n else 0,
        "groups": {label: breakdown(f) for label, f in [
            ("Role type", "role"), ("Employer", "company"), ("Where JobApp found it", "source"),
            ("Resume", "resume"), ("Fit score", "fit"), ("Follow-up", "followed_up")]},
        "enough": n >= 20,
    }
