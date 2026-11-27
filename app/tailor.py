"""Tailor resume + cover letter for one job.

Backends (config.yaml -> tailor.backend):
  claude_cli  uses your Claude plan through `claude -p` (no API credits). Runs in safe mode
              so plugins/MCP/skills aren't loaded: ~2-10K tokens per job instead of 100K+.
  api         uses an Anthropic API key from .env (ANTHROPIC_API_KEY). Billed per token.

Truthfulness is enforced in code, not just asked for: every resume bullet must cite the profile
bullet(s) it came from, and skills must exist in your profile. Anything else is dropped.
"""
import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import humanize
from .config import ROOT, load_config
from .profile import Profile, load_profile
from .text import has_term

log = logging.getLogger(__name__)

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["keywords", "summary", "skills", "sections", "cover_letter", "needs"],
    "properties": {
        "keywords": {"type": "array", "items": {"type": "string"},
                     "description": "10-20 ATS keywords/phrases from the posting, most important first"},
        "summary": {"type": "string", "description": "2-line resume summary"},
        "skills": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["category", "items"],
            "properties": {"category": {"type": "string"},
                           "items": {"type": "array", "items": {"type": "string"}}}}},
        "sections": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["entry_id", "bullets"],
            "properties": {
                "entry_id": {"type": "string"},
                "bullets": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False, "required": ["text", "source"],
                    "properties": {"text": {"type": "string"},
                                   "source": {"type": "array", "items": {"type": "string"}}}}}}}},
        "cover_letter": {"type": "string"},
        "needs": {"type": "array", "items": {"type": "string"},
                  "description": "Missing specifics the candidate should supply, one per item"},
    },
}

SYSTEM = """You tailor a student's resume and cover letter to one internship posting.

ABSOLUTE RULES (truthfulness)
- Use ONLY facts in the CANDIDATE PROFILE. Never invent metrics, tools, employers, dates, duties,
  team sizes, outcomes or company facts. Rephrasing and reordering is fine; new claims are not.
- Every resume bullet must list in "source" the profile bullet ID(s) it is based on (e.g. "exp0.b1").
  A bullet may merge or shorten its sources but not add facts beyond them.
- Skills must come from the profile's SKILLS list, spelled the same way. If the posting asks for a
  skill the candidate doesn't list, do NOT add it; mention it in "needs" only if it's a key requirement.
- Where a specific (number, scale, result) would make a point much stronger and the profile lacks it,
  write [NEEDS: short question] inline and add the same question to "needs".
- Bullets marked UNCONFIRMED may be used, but prefer confirmed bullets when they serve equally well.
- This applies to the cover letter too, including "human texture": no invented anecdotes, feelings,
  preferences, habits or backstory ("nobody gave me a template", "the part I love most", "I've
  always been fascinated"). Reasons for wanting the role may only come from MOTIVATION in the
  profile or from a factual link between the posting and the profile; otherwise use
  [NEEDS: why this role/company appeals to you].
- Use absolute dates ("October 2026"), never relative ones ("last month"), since letters get reused.

RESUME (ATS-first, one page)
- "keywords": the 10-20 terms an ATS would match for this posting (tools, methods, domain words).
- Work the keywords into the summary, skills order and bullets wherever the profile genuinely supports it.
- It must fit on ONE page: "sections" includes EVERY experience entry (exp*) and the 3 most relevant
  projects (proj*), most relevant first; 2-3 bullets per entry, at most 13 bullets in total, each
  bullet under ~25 words. Start bullets with a plain past-tense verb (built, analyzed,
  led, cleaned, compared); present tense for current roles. No first person.
- Plain words (see the humanize rules): never "spearheaded", "leveraged", "utilized", "showcased".
- "summary": two lines max, specific to this role, no adjectives like "passionate" or "results-driven".
- "skills": 3-5 categories, reordered so the posting's tools come first; drop irrelevant ones.

COVER LETTER (humanize skill, draft mode)
- Follow the HUMANIZE RULES below exactly: draft mode for a cover letter.
- Format: "Dear Hiring Manager," ... sign off "Best," then the candidate's full name. Under 300 words, plain text,
  no markdown, no address block.
- Name the actual role and company. Use one concrete thing from the posting itself; never generic
  mission praise. Build it around one or two real achievements from the profile.
- Mention availability (January to August 2027, 4 or 8 months) once, briefly.

HUMANIZE RULES (the candidate's own skill; apply to the cover letter, and word choice to the resume)
"""


def build_system() -> str:
    return SYSTEM + humanize.rules_text()


def build_user(job: dict, profile: Profile) -> str:
    desc = (job.get("description") or "").strip()
    if len(desc) > 9000:
        desc = desc[:9000] + "\n[posting truncated]"
    return (f"CANDIDATE PROFILE\n{profile.as_prompt()}\n\n"
            f"JOB POSTING\nRole: {job['title']}\nCompany: {job['company']}\n"
            f"Location: {job.get('location') or ''}\n\n{desc or '[No description captured]'}\n\n"
            "Return the JSON object described by the schema.")


# ---------------- backends ----------------
class TailorError(RuntimeError):
    pass


def _claude_cli(system: str, user: str, model: str, effort: str, timeout: int, schema: dict = None) -> tuple[dict, dict]:
    exe = shutil.which("claude")
    if not exe:
        raise TailorError("Claude Code CLI ('claude') not found on PATH. Install it or switch "
                          "tailor.backend to 'api' in config.yaml.")
    with tempfile.TemporaryDirectory() as tmp:
        sp = Path(tmp) / "system.md"
        sp.write_text(system, encoding="utf-8")
        cmd = [exe, "-p", "--safe-mode", "--strict-mcp-config", "--output-format", "json",
               "--system-prompt-file", str(sp), "--tools", "", "--no-session-persistence",
               "--model", model, "--effort", effort, "--json-schema", json.dumps(schema or SCHEMA)]
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}  # force your plan login
        try:
            p = subprocess.run(cmd, input=user, capture_output=True, text=True, encoding="utf-8",
                               cwd=tmp, timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            raise TailorError(f"Claude took longer than {timeout}s. Try again.")
    try:
        out = json.loads(p.stdout)
    except json.JSONDecodeError:
        raise TailorError(f"Unexpected CLI output: {(p.stdout or p.stderr)[:400]}")
    if out.get("is_error") or not out.get("structured_output"):
        msg = out.get("result") or p.stderr or "no structured output"
        if "limit" in str(msg).lower():
            msg = f"Claude plan usage limit reached: {msg}"
        raise TailorError(str(msg)[:400])
    u = out.get("usage", {})
    meta = {"cost_usd": out.get("total_cost_usd"), "billing": "Claude plan (notional cost)",
            "input_tokens": u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
            + u.get("cache_read_input_tokens", 0), "output_tokens": u.get("output_tokens", 0)}
    return out["structured_output"], meta


def _api(system: str, user: str, model: str, effort: str, prices: dict, schema: dict = None) -> tuple[dict, dict]:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise TailorError("No ANTHROPIC_API_KEY in .env. Add it, or set tailor.backend: claude_cli.")
    import anthropic

    client = anthropic.Anthropic()
    kwargs = dict(model=model, max_tokens=16000,
                  system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                  messages=[{"role": "user", "content": user}],
                  output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema or SCHEMA}})
    try:
        if "haiku" in model:
            resp = client.messages.create(**kwargs)
        else:  # retry on a fallback model if the request is declined for policy reasons
            resp = client.beta.messages.create(**kwargs, betas=["server-side-fallback-2026-07-01"],
                                               fallbacks="default")
    except anthropic.AuthenticationError:
        raise TailorError("API key rejected. Check ANTHROPIC_API_KEY in .env.")
    except anthropic.RateLimitError:
        raise TailorError("Rate limited by the API. Wait a minute and try again.")
    except anthropic.APIStatusError as e:
        raise TailorError(f"API error {e.status_code}: {e.message}"[:400])
    except anthropic.APIConnectionError:
        raise TailorError("Couldn't reach the Anthropic API. Check your internet connection.")
    if resp.stop_reason == "refusal":
        raise TailorError("The model declined this request.")
    if resp.stop_reason == "max_tokens":
        raise TailorError("Output was cut off (max_tokens). Try again.")
    text = next((b.text for b in resp.content if b.type == "text"), "")
    u = resp.usage
    p = prices.get(resp.model, prices.get(model, {"input": 0, "output": 0}))
    cache_w = getattr(u, "cache_creation_input_tokens", 0) or 0
    cache_r = getattr(u, "cache_read_input_tokens", 0) or 0
    cost = (u.input_tokens * p["input"] + cache_w * p["input"] * 1.25 + cache_r * p["input"] * 0.1
            + u.output_tokens * p["output"]) / 1e6
    meta = {"cost_usd": round(cost, 4), "billing": "API credits",
            "input_tokens": u.input_tokens + cache_w + cache_r, "output_tokens": u.output_tokens}
    return json.loads(text), meta


# ---------------- validation + scoring ----------------
def validate(draft: dict, profile: Profile) -> dict:
    """Drop anything not traceable to the profile. Returns the cleaned draft with `warnings`."""
    warnings = []
    allowed = {s.lower(): s for items in profile.skills.values() for s in items}
    skills = []
    for cat in draft.get("skills", []):
        keep = []
        for s in cat.get("items", []):
            if s.lower() in allowed:
                keep.append(allowed[s.lower()])
            else:
                warnings.append(f"Removed skill not in your profile: {s}")
        if keep:
            skills.append({"category": cat["category"], "items": keep})

    sections, seen = [], set()
    for sec in draft.get("sections", []):
        eid = sec.get("entry_id", "")
        entry = profile.entries.get(eid)
        if not entry or eid in seen:
            continue
        seen.add(eid)
        bullets = []
        for b in sec.get("bullets", []):
            src = [s for s in b.get("source", []) if s.startswith(eid + ".") and profile.bullet(s)]
            if not src:
                warnings.append(f"Removed bullet with no valid source in {entry.heading}: {b.get('text', '')[:60]}")
                continue
            bullets.append({"text": b["text"].strip(), "source": src,
                            "unconfirmed": any(s in entry.unconfirmed for s in src)})
        if bullets:
            sections.append({"entry_id": eid, "bullets": bullets})
    # Never silently drop a job from the experience section.
    for e in profile.entries.values():
        if e.kind == "experience" and e.id not in seen:
            sections.append({"entry_id": e.id, "bullets": [
                {"text": t, "source": [bid], "unconfirmed": bid in e.unconfirmed}
                for bid, t in e.bullets.items()]})
            warnings.append(f"Re-added missing experience: {e.heading}")
    draft["skills"], draft["sections"] = skills, sections
    draft["warnings"] = warnings
    return draft


def resume_prose(draft: dict) -> str:
    """Only the text the model wrote (summary + bullets), for the AI-tells scan."""
    return "\n".join([draft.get("summary", "")] +
                     [b["text"] for s in draft.get("sections", []) for b in s["bullets"]])


def resume_text(draft: dict, profile: Profile) -> str:
    lines = [draft.get("summary", "")]
    for c in draft.get("skills", []):
        lines.append(f"{c['category']}: {', '.join(c['items'])}")
    for sec in draft.get("sections", []):
        e = profile.entries[sec["entry_id"]]
        lines.append(f"{e.heading} {' '.join(e.stack)}")
        lines += [b["text"] for b in sec["bullets"]]
    return "\n".join(lines)


def score(draft: dict, profile: Profile) -> dict:
    text = resume_text(draft, profile)
    kws = draft.get("keywords", [])
    matched = [k for k in kws if has_term(text, k)]
    missing = [k for k in kws if k not in matched]
    # Missing keywords you DO have somewhere in the profile are fixable; others are true gaps.
    prof_text = profile.as_prompt()
    fixable = [k for k in missing if has_term(prof_text, k)]
    return {
        "ats": {"coverage": round(100 * len(matched) / len(kws)) if kws else 0,
                "matched": matched, "missing": missing, "fixable": fixable},
        "letter": humanize.check(draft.get("cover_letter", ""), "letter"),
        "resume": humanize.check(resume_prose(draft), "resume"),
    }


def call_model(system: str, user: str, schema: dict, max_effort: str | None = None) -> tuple[dict, dict]:
    """Run one structured request on the configured backend (your Claude plan or the API)."""
    cfg = load_config().get("tailor", {})
    model, effort = cfg.get("model", "claude-opus-5-5"), max_effort or cfg.get("effort", "medium")
    if cfg.get("backend", "claude_cli") == "api":
        raw, meta = _api(system, user, model, effort, cfg.get("prices", {}), schema)
    else:
        raw, meta = _claude_cli(system, user, model, effort, cfg.get("timeout_seconds", 600), schema)
    meta.update(backend=cfg.get("backend", "claude_cli"), model=model, effort=effort)
    return raw, meta


def tailor(job: dict) -> dict:
    """Returns {"draft": {...}, "scores": {...}, "meta": {...}}. Raises TailorError."""
    cfg = load_config().get("tailor", {})
    profile = load_profile()
    system, user = build_system(), build_user(job, profile)
    backend = cfg.get("backend", "claude_cli")
    model, effort = cfg.get("model", "claude-opus-5-5"), cfg.get("effort", "medium")
    if backend == "api":
        raw, meta = _api(system, user, model, effort, cfg.get("prices", {}))
    else:
        raw, meta = _claude_cli(system, user, model, effort, cfg.get("timeout_seconds", 600))
    meta.update(backend=backend, model=model, effort=effort)
    draft = validate(raw, profile)
    return {"draft": draft, "scores": score(draft, profile), "meta": meta}
