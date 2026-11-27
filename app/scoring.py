"""Filtering (keep/drop) and fit scoring (0-100) for job postings."""
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import re

import yaml

from .config import ROOT
from .text import any_term, has_term, is_canada, is_gta, is_remote, location_ok, norm_company


def is_student_role(title: str, cfg: dict) -> bool:
    s = cfg["search"]
    return bool(any_term(title, s["student_terms"])) and not any_term(title, s["exclude_title_terms"])


def field_terms(cfg: dict) -> list[str]:
    return cfg["search"]["core_terms"] + cfg["search"]["broad_terms"]


def keep_title(title: str, cfg: dict) -> bool:
    return is_student_role(title, cfg) and bool(any_term(title, field_terms(cfg)))


_DEG = r"(?:ph\.?\s?d\.?|doctoral|doctorate|m\.?\s?sc\.?|m\.?\s?eng\.?|master'?s|masters|graduate)"
_GRAD_ONLY = [
    re.compile(rf"\b(?:enrolled in|pursuing|working towards?|completing)\s+(?:a|an|your)?\s*{_DEG}", re.I),
    re.compile(rf"\b{_DEG}(?:\s+(?:student|candidate|degree)s?)?\s*(?:or|/|and)\s*{_DEG}\s+(?:student|candidate|program|level)", re.I),
    re.compile(r"\b(?:ph\.?\s?d|master'?s|m\.?\s?sc|graduate)\s+(?:student|candidate|researcher)s?\b", re.I),
]
_UNDERGRAD = re.compile(r"\b(?:bachelor'?s?|undergrad(?:uate)?s?|b\.?\s?sc|b\.?\s?eng|diploma|college)\b", re.I)


def grad_only(text: str | None) -> bool:
    """True when a posting is aimed only at Master's/PhD students (no mention of undergrads).
    You're an undergrad, so those are skipped entirely."""
    if not text:
        return False
    return any(p.search(text) for p in _GRAD_ONLY) and not _UNDERGRAD.search(text)


def keep(job, cfg: dict) -> bool:
    """Hard filter: student role, relevant field (in title), Canada/remote location,
    and not a Master's/PhD-only internship."""
    return (keep_title(job.title, cfg) and location_ok(job.location, job.is_remote)
            and not grad_only(job.description))


@lru_cache
def _profile_skills() -> tuple[str, ...]:
    try:
        from .profile import profile_path
        with open(profile_path(), encoding="utf-8") as f:
            p = yaml.safe_load(f)
        return tuple(sk for group in p.get("skills", {}).values() for sk in group)
    except Exception:
        return ()


def _is_preferred(company: str, cfg: dict) -> bool:
    c = norm_company(company)
    for name in cfg.get("preferred_companies", []):
        n = norm_company(name)
        if n and (c == n or has_term(c, n)):
            return True
    return False


def score(job, cfg: dict) -> tuple[int, list[str]]:
    s = cfg["search"]
    pts, why = 0, []
    text = f"{job.title}\n{job.description or ''}"

    core = any_term(job.title, s["core_terms"])
    broad = any_term(job.title, s["broad_terms"])
    if core:
        pts += 30; why.append(f"+30 core field in title ({', '.join(core[:3])})")
    elif broad:
        pts += 15; why.append(f"+15 related field in title ({', '.join(broad[:3])})")
    off = any_term(job.title, s["off_field_terms"])
    if off:
        pts -= 15; why.append(f"-15 outside your target fields ({off[0]})")
    desc_core = any_term(job.description or "", s["core_terms"])
    if desc_core:
        p = min(10, 2 * len(desc_core)); pts += p; why.append(f"+{p} core terms in description")

    if is_gta(job.location):
        pts += 20; why.append("+20 GTA")
    elif is_remote(job.location, job.is_remote):
        pts += 15; why.append("+15 remote")
    elif is_canada(job.location):
        pts += 5; why.append("+5 elsewhere in Canada")

    good = any_term(text, s["good_term_terms"])
    bad = any_term(text, s["bad_term_terms"])
    if bad:  # e.g. a 12–16 month or Fall 2027 term. You're only free Jan–Aug 2027
        pts -= 30; why.append(f"-30 term doesn't fit Jan–Aug 2027 ({bad[0]})")
    elif good:
        pts += 15; why.append(f"+15 term fits ({good[0]})")

    if _is_preferred(job.company, cfg):
        pts += 15; why.append("+15 well-known employer")

    skills = [sk for sk in _profile_skills() if has_term(job.description or "", sk)]
    if skills:
        p = min(10, len(skills)); pts += p; why.append(f"+{p} your skills ({', '.join(skills[:4])})")

    if job.posted_at:
        try:
            dt = datetime.fromisoformat(str(job.posted_at).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - dt < timedelta(hours=36):
                pts += 5; why.append("+5 posted in last 36h")
        except ValueError:
            pass

    return max(0, min(100, pts)), why
