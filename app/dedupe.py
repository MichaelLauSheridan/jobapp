"""Detect the same job posted on several sites (LinkedIn + Indeed + company board, etc.)."""
import sqlite3

from rapidfuzz import fuzz

from .text import CANADA_HINTS, GTA, is_remote, norm_company, norm_title

TITLE_SAME = 92      # titles this similar (after normalization) = same job
TITLE_MAYBE = 80     # ...this similar = same job only if descriptions also match
DESC_SAME = 85


def _places(loc: str) -> set[str]:
    l = (loc or "").lower()
    places = {p for p in set(GTA) | set(CANADA_HINTS) if p in l} - {"canada", "ontario", "gta"}
    # Collapse all GTA cities to one bucket: the same role is often listed as Toronto on one
    # site and Mississauga on another.
    if places & set(GTA):
        places = (places - set(GTA)) | {"gta"}
    return places


def locations_compatible(a: str, b: str, a_remote=False, b_remote=False) -> bool:
    pa, pb = _places(a), _places(b)
    if not pa or not pb:
        return True
    if is_remote(a, a_remote) and is_remote(b, b_remote):
        return True
    return bool(pa & pb)


def is_same(a: dict, b: dict) -> bool:
    """a, b: dicts with title, company, location, is_remote, description."""
    if norm_company(a["company"]) != norm_company(b["company"]):
        if fuzz.ratio(norm_company(a["company"]), norm_company(b["company"])) < 90:
            return False
    if not locations_compatible(a.get("location"), b.get("location"),
                                a.get("is_remote"), b.get("is_remote")):
        return False
    # token_sort (not token_set): "Business Analyst" must NOT match "Business Enablement Analyst".
    t = fuzz.token_sort_ratio(norm_title(a["title"]), norm_title(b["title"]))
    if t >= TITLE_SAME:
        return True
    if t >= TITLE_MAYBE:
        da, db = (a.get("description") or "")[:2000], (b.get("description") or "")[:2000]
        if len(da) > 200 and len(db) > 200 and fuzz.ratio(da, db) >= DESC_SAME:
            return True
    return False


def find_duplicate(conn: sqlite3.Connection, job: dict) -> int | None:
    cn = norm_company(job["company"])
    first = cn.split(" ")[0] if cn else ""
    rows = conn.execute(
        "SELECT id, title, company, location, is_remote, description FROM jobs "
        "WHERE (company_norm = ? OR company_norm LIKE ?) AND found_at >= datetime('now', '-90 days')",
        (cn, f"{first}%"),
    ).fetchall()
    for r in rows:
        if is_same(job, dict(r)):
            return r["id"]
    return None
