"""Load profile/master.yaml and give every bullet a stable ID, so tailored output can be traced
back to a real fact (and anything that can't be traced is rejected)."""
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .config import ROOT

PROFILE_DIR = ROOT / "profile"


def profile_path() -> Path:
    """Which profile to use: $JOBAPP_PROFILE if set (the tests use this), otherwise your own
    master.yaml, otherwise the fictional example that ships with the repo."""
    if os.environ.get("JOBAPP_PROFILE"):
        return Path(os.environ["JOBAPP_PROFILE"])
    real = PROFILE_DIR / "master.yaml"
    return real if real.exists() else PROFILE_DIR / "master.example.yaml"


@dataclass
class Entry:
    id: str            # "exp0", "proj2"
    kind: str          # "experience" | "project"
    heading: str       # "Data Analyst Intern | Example Nonprofit"
    dates: str
    stack: list[str]
    link: str = ""     # project repo / demo URL shown under the heading
    bullets: dict[str, str] = field(default_factory=dict)       # "exp0.b1" -> text
    unconfirmed: set[str] = field(default_factory=set)          # bullet ids with confirm: true


@dataclass
class Profile:
    raw: dict
    entries: dict[str, Entry]
    skills: dict[str, list[str]]

    @property
    def all_skills(self) -> set[str]:
        return {s.lower() for items in self.skills.values() for s in items}

    def bullet(self, bid: str) -> str | None:
        entry = self.entries.get(bid.split(".")[0])
        return entry.bullets.get(bid) if entry else None

    def as_prompt(self) -> str:
        """Compact, ID-tagged text of every fact the model may use."""
        r, out = self.raw, []
        c = r["contact"]
        out.append(f"NAME: {c['name']} | {c.get('title', '')} | {c.get('location', '')}")
        a = r.get("availability", {})
        out.append(f"AVAILABILITY: {a.get('term', '')} ({', '.join(a.get('durations', []))})")
        for e in r.get("education", []):
            out.append(f"EDUCATION: {e['degree']}, {e['school']}, graduating {e['graduation']}. "
                       f"Coursework: {', '.join(e.get('coursework', []))}")
        for aw in r.get("awards", []):
            out.append(f"AWARD: {aw['name']} ({aw.get('date', '')}). {aw.get('detail', '')}")
        mot = [m for m in (r.get("motivation") or []) if m]
        out.append("MOTIVATION (the candidate's own words; the only allowed source for why they want a role): "
                   + (" | ".join(mot) if mot else "none given"))
        out.append("SKILLS (you may only list skills from here):")
        for cat, items in self.skills.items():
            if cat == ADDED_CATEGORY:
                used = {a["skill"]: a.get("where", "") for a in load_added_skills()}
                items = [f"{s} (used in: {used[s]})" if used.get(s) else s for s in items]
            out.append(f"  {cat}: {', '.join(items)}")
        for e in self.entries.values():
            out.append(f"\n[{e.id}] {e.kind.upper()}: {e.heading} | {e.dates}"
                       + (f" | stack: {', '.join(e.stack)}" if e.stack else ""))
            ctx = next((x.get("context") for x in r.get(e.kind if e.kind == "experience" else "projects", [])
                        if _heading(x, e.kind) == e.heading and x.get("context")), None)
            if ctx:
                out.append(f"  context: {' '.join(ctx.split())}")
            for bid, text in e.bullets.items():
                flag = " (UNCONFIRMED by candidate)" if bid in e.unconfirmed else ""
                out.append(f"  {bid}: {text}{flag}")
        return "\n".join(out)


def _heading(x: dict, kind: str) -> str:
    return f"{x['title']} | {x['company']}" if kind == "experience" else x["name"]


def load_profile() -> Profile:
    with open(profile_path(), encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    entries: dict[str, Entry] = {}
    for kind, key, prefix in (("experience", "experience", "exp"), ("project", "projects", "proj")):
        for i, x in enumerate(raw.get(key, [])):
            eid = f"{prefix}{i}"
            if x.get("hide"):   # kept in the file (e.g. for interviews) but never used on resumes/letters
                continue
            dates = (f"{x.get('start', '')} – {x.get('end', '')}".strip(" –") if kind == "experience"
                     else str(x.get("date", "")))
            e = Entry(id=eid, kind=kind, heading=_heading(x, kind), dates=dates, stack=x.get("stack", []),
                      link=x.get("link", ""))
            for j, b in enumerate(x.get("bullets", [])):
                bid = f"{eid}.b{j}"
                e.bullets[bid] = b["text"] if isinstance(b, dict) else str(b)
                if (isinstance(b, dict) and b.get("confirm")) or x.get("confirm"):
                    e.unconfirmed.add(bid)
            entries[eid] = e

    skills = {cat: list(items) for cat, items in (raw.get("skills") or {}).items()}
    added = load_added_skills()
    if added:
        skills[ADDED_CATEGORY] = [a["skill"] for a in added]
    return Profile(raw=raw, entries=entries, skills=skills)


# ---- skills you add from the draft page ("I have this") ----
# Kept in their own file next to the profile, so the app never rewrites your hand-edited
# master.yaml. For profile/master.yaml that's profile/master.added.local.yaml (git-ignored).
ADDED_CATEGORY = "added_by_you"


def added_skills_path() -> Path:
    p = profile_path()
    return p.with_name(f"{p.stem}.added.local.yaml")


def load_added_skills() -> list[dict]:
    path = added_skills_path()
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or []


def add_skill(skill: str, where: str) -> bool:
    """Record a skill you confirmed you have, with where you used it. Returns False if the
    profile already lists it."""
    skill = skill.strip()
    if not skill or skill.lower() in load_profile().all_skills:
        return False
    added = load_added_skills()
    added.append({"skill": skill, "where": where.strip(),
                  "added": __import__("datetime").date.today().isoformat()})
    path = added_skills_path()
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Skills you confirmed with \"I have this\" on a draft page. Edit or delete freely.\n")
        yaml.safe_dump(added, f, allow_unicode=True, sort_keys=False)
    return True
