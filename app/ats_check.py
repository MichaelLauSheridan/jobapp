"""Read the approved resume PDF back the way an ATS (applicant tracking system) would, and check
that the important things survive: contact details, section order, keywords, one page."""
import re
from pathlib import Path

from .text import has_term

SECTIONS = ["SUMMARY", "TECHNICAL SKILLS", "EDUCATION", "PROFESSIONAL EXPERIENCE", "PROJECTS"]


def extract_text(pdf_path: str) -> tuple[str, int]:
    from pypdf import PdfReader
    r = PdfReader(pdf_path)
    return "\n".join((p.extract_text() or "") for p in r.pages), len(r.pages)


def check(pdf_path: str, contact: dict, keywords: list[str]) -> dict:
    """Returns {"ok": bool, "checks": [(label, passed, detail)], "keyword_coverage": int}."""
    if not pdf_path or not Path(pdf_path).exists():
        return {"ok": False, "checks": [("PDF exists", False, "No PDF to check (was PDF export on?)")],
                "keyword_coverage": 0}
    text, pages = extract_text(pdf_path)
    flat = re.sub(r"\s+", " ", text)
    checks = []

    def add(label, ok, detail=""):
        checks.append((label, bool(ok), detail))

    add("Text is selectable (not an image)", len(flat) > 400, f"{len(flat)} characters read")
    add("Fits on one page", pages == 1, f"{pages} page(s)")
    add("Name readable", contact["name"].lower() in flat.lower())
    add("Email readable", contact.get("email", "").lower() in flat.lower())
    digits = re.sub(r"\D", "", contact.get("phone", ""))
    add("Phone readable", digits and digits in re.sub(r"\D", "", flat))
    for key in ("linkedin", "github"):
        url = (contact.get(key) or "").replace("https://", "").replace("www.", "").rstrip("/")
        if url:
            add(f"{key.capitalize()} readable", url.lower() in flat.lower().replace(" ", ""))
    # Section headings present and in reading order (catches two-column/table layouts that ATSs scramble).
    pos = [(s, flat.upper().find(s)) for s in SECTIONS]
    found = [(s, p) for s, p in pos if p >= 0]
    missing = [s.title() for s, p in pos if p < 0 and s != "SUMMARY"]
    add("Standard section headings", not missing, "missing: " + ", ".join(missing) if missing else "")
    in_order = [p for _, p in found] == sorted(p for _, p in found)
    add("Sections read in the right order", in_order)
    # Characters ATSs often mangle.
    odd = sorted(set(re.findall(r"[ﬀ-ﬆ�□]", text)))
    add("No garbled characters", not odd, " ".join(odd))
    kw_hit = [k for k in keywords if has_term(flat, k)]
    coverage = round(100 * len(kw_hit) / len(keywords)) if keywords else 0
    return {"ok": all(c[1] for c in checks), "checks": checks, "keyword_coverage": coverage,
            "keywords_missing": [k for k in keywords if k not in kw_hit]}
