"""Humanize support.

1. `rules_text()` loads YOUR humanize skill (~/.claude/skills/humanize) so the tailoring prompt uses
   exactly the same rules as when you ask Claude directly. Edit the skill and the app follows.
2. `check()` is a free, local scan for the most visible AI tells (no tokens used). It mirrors
   the skill's hard rules and banned-pattern list; it's a guide, not a detector score.
"""
import re
import statistics
from pathlib import Path

SKILL_DIR = Path.home() / ".claude" / "skills" / "humanize"

FALLBACK_RULES = """Write like a real person: no em or en dashes; vary sentence length (some under 6
words, some over 25); use contractions; at most one 'A, B, and C' list; no 'it's not X, it's Y'
frames; no filler transitions (Moreover, Furthermore, Additionally); no stock openers or closers;
plain everyday words (use, help, show) instead of leverage/utilize/facilitate/showcase; never
invent facts, flag missing specifics as [NEEDS: ...]."""


def rules_text() -> str:
    parts = []
    for name in ("SKILL.md", "references/banned-patterns.md"):
        p = SKILL_DIR / name
        if p.exists():
            t = p.read_text(encoding="utf-8")
            t = re.sub(r"\A---.*?---\s*", "", t, flags=re.S)  # drop frontmatter
            parts.append(t)
    return "\n\n".join(parts) if parts else FALLBACK_RULES


# ---- local checker ----
BANNED = """delve delves delving underscore underscores underscored showcase showcasing showcased
meticulous meticulously intricate commendable leverage leveraging leveraged utilize utilizing
harness foster facilitate streamline spearhead spearheaded empower elevate unlock unleash
revolutionize embark bolster resonate reimagine seamless seamlessly robust comprehensive
multifaceted pivotal paramount crucial nuanced transformative revolutionary cutting-edge
game-changer game-changing state-of-the-art holistic bespoke curated vibrant dynamic unparalleled
groundbreaking tapestry realm testament cornerstone beacon synergy myriad plethora interplay
paradigm passionate innovative""".split()
PHRASES = ["a wide range of", "a variety of", "it's worth noting", "it is worth noting",
           "it's important to note", "plays a key role", "plays a crucial role", "plays a vital role",
           "i am writing to express", "i am excited to apply", "i'm excited to apply",
           "aligns with your mission", "in today's fast-paced", "when it comes to", "at the end of the day",
           "in conclusion", "not only", "don't hesitate", "feel free to reach out", "i hope this",
           "thank you for your time and consideration", "in order to", "the power of", "deeply passionate"]
TRANSITIONS = ["moreover", "furthermore", "additionally", "consequently", "thus", "hence", "notably",
               "indeed", "importantly", "ultimately", "essentially", "nevertheless", "nonetheless",
               "that being said", "that said"]
CONTRAST = re.compile(r"\b(it's|it is|this is|isn't|wasn't)\s+not\s+(just\s+)?\w+[^.]{0,40}?,\s*(it's|but)\b|"
                      r"\bnot just\b[^.]{0,60}\bbut\b", re.I)
CONTRACTION = re.compile(r"\b\w+'(m|re|s|t|ve|ll|d)\b", re.I)


def _sentences(text: str) -> list[str]:
    body = re.sub(r"^(dear|hi|hello)\b.*$|^(sincerely|best|thanks|regards|cheers)\b.*$", "", text,
                  flags=re.I | re.M)
    return [s for s in re.split(r"(?<=[.!?])\s+|\n{2,}", body) if len(s.split()) >= 2]


def check(text: str, kind: str = "letter") -> dict:
    """kind: 'letter' (full prose rules) or 'resume' (word-level rules only)."""
    issues = []
    low = text.lower()
    dashes = len(re.findall(r"[–—]", text))
    if dashes:
        issues.append(f"{dashes} em/en dash(es): rewrite those sentences without them")
    words = sorted({w for w in BANNED if re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", low)})
    if words:
        issues.append("AI-favourite words: " + ", ".join(words))
    phrases = [p for p in PHRASES if p in low]
    if phrases:
        issues.append("Stock phrases: " + "; ".join(f'"{p}"' for p in phrases))
    if kind == "letter":
        trans = [t for t in TRANSITIONS if re.search(r"(^|[.!?]\s+)" + t + r"\b", low)]
        if len(trans) > 1:
            issues.append("More than one filler transition: " + ", ".join(trans))
        if CONTRAST.search(text):
            issues.append('Contrast frame ("it\'s not X, it\'s Y" / "not just X but Y")')
        triads = len(re.findall(r"\b\w+(?: \w+)?, \w+(?: \w+)?,? and \w+", text))
        if triads > 1:
            issues.append(f"{triads} 'A, B, and C' lists (keep at most one)")
        lens = [len(s.split()) for s in _sentences(text)]
        if len(lens) >= 4:
            if max(lens) < 4 * max(min(lens), 1):
                issues.append(f"Sentence lengths too even ({min(lens)}–{max(lens)} words); "
                              "add a short one and a long one")
            for i in range(len(lens) - 2):
                trio = lens[i:i + 3]
                if max(trio) - min(trio) <= 3:
                    issues.append(f"Sentences {i + 1}–{i + 3} are about the same length ({trio})")
                    break
        if not CONTRACTION.search(text):
            issues.append("No contractions (I'm, don't, it's): reads stiff")
        n_words = len(text.split())
        if n_words > 330:
            issues.append(f"{n_words} words; aim for under ~300")
        stdev = statistics.pstdev(lens) if len(lens) > 1 else 0
    else:
        stdev = 0
    needs = re.findall(r"\[NEEDS:[^\]]*\]", text)
    score = max(0, 100 - 12 * len(issues))
    return {"score": score, "issues": issues, "needs": needs, "sentence_stdev": round(stdev, 1)}
