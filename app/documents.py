"""Write the approved resume and cover letter as .docx (and PDF via Word), in the same visual style
as your original resume: Times New Roman, centred name header, bold section titles, bullets."""
import logging
import re
from datetime import date
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.oxml.ns import qn
from docx.shared import Inches, Pt

from .config import ROOT
from .profile import Profile

log = logging.getLogger(__name__)
OUTPUT_DIR = ROOT / "output"
FONT = "Times New Roman"


def _safe(s: str, n: int = 40) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")[:n]


def out_dir_for(job: dict) -> Path:
    d = OUTPUT_DIR / f"{date.today():%Y-%m-%d}_{_safe(job['company'], 25)}_{_safe(job['title'])}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _new_doc() -> Document:
    doc = Document()
    s = doc.sections[0]
    s.page_width, s.page_height = Inches(8.5), Inches(11)
    s.left_margin = s.right_margin = Inches(0.56)
    s.top_margin = s.bottom_margin = Inches(0.42)
    st = doc.styles["Normal"]
    st.font.name, st.font.size = FONT, Pt(10.5)
    st.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
    st.paragraph_format.space_after = Pt(0)
    st.paragraph_format.space_before = Pt(0)
    return doc


def _run(p, text, bold=False, size=None, italic=False):
    r = p.add_run(text)
    r.bold, r.italic = bold, italic
    r.font.name = FONT
    if size:
        r.font.size = Pt(size)
    return r


def _heading(doc, text):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after = Pt(2)
    _run(p, text.upper(), bold=True, size=12)
    # bottom border line under section titles
    pPr = p._p.get_or_add_pPr()
    bdr = pPr.makeelement(qn("w:pBdr"), {})
    bottom = bdr.makeelement(qn("w:bottom"), {qn("w:val"): "single", qn("w:sz"): "6",
                                              qn("w:space"): "1", qn("w:color"): "000000"})
    bdr.append(bottom)
    pPr.append(bdr)


def _link(p, url, text):
    """Clickable hyperlink run (also readable as plain text by ATS parsers)."""
    rid = p.part.relate_to(url, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
                           is_external=True)
    h = p._p.makeelement(qn("w:hyperlink"), {qn("r:id"): rid})
    r = _run(p, text, italic=True)
    r.font.underline = True
    h.append(r._r)
    p._p.append(h)


def _entry_title(doc, left, right="", sub="", link=""):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(3)
    p.paragraph_format.tab_stops.add_tab_stop(Inches(7.38), WD_TAB_ALIGNMENT.RIGHT)
    _run(p, left, bold=True)
    if right:
        _run(p, "\t" + right)
    if sub:
        p.add_run("\n")
        _run(p, sub, italic=True)
    if link:
        p.add_run("  |  " if sub else "\n")
        _link(p, link, re.sub(r"^https?://(www\.)?", "", link))


def _bullet(doc, text):
    p = doc.add_paragraph(style="List Bullet")
    p.paragraph_format.space_after = Pt(0)
    _run(p, text)


def _fmt_dates(d: str) -> str:
    months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()

    def one(x):
        m = re.fullmatch(r"(\d{4})-(\d{2})", x.strip())
        return f"{months[int(m.group(2)) - 1]} {m.group(1)}" if m else x.strip()

    return " – ".join(one(x) for x in d.split("–")) if d else ""


def write_resume(draft: dict, profile: Profile, path: Path):
    r, doc = profile.raw, _new_doc()
    c = r["contact"]
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _run(p, c["name"].upper(), bold=True, size=22)
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _run(p, c.get("title", ""), bold=True)
    contact = [c.get("location"), c.get("phone"), c.get("email"), c.get("linkedin"), c.get("github")]
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _run(p, " • ".join(x for x in contact if x), size=10)

    if draft.get("summary"):
        _heading(doc, "Summary")
        _run(doc.add_paragraph(), draft["summary"])

    _heading(doc, "Technical Skills")
    for cat in draft.get("skills", []):
        p = doc.add_paragraph()
        _run(p, f"{cat['category']}: ", bold=True)
        _run(p, ", ".join(cat["items"]))

    _heading(doc, "Education")
    for e in r.get("education", []):
        _entry_title(doc, f"{e['degree']}, {e['school']}", f"Expected {e['graduation']}")
        if e.get("coursework"):
            p = doc.add_paragraph()
            _run(p, "Relevant Coursework: ", bold=True)
            _run(p, ", ".join(e["coursework"]))
    headings = " ".join(profile.entries[s["entry_id"]].heading.lower() for s in draft.get("sections", []))
    for aw in r.get("awards", []):
        # Skip if a selected project already shows this award (e.g. "SpaceSense ... (Hackathon Winner)").
        if any(len(w) > 6 and w in headings for w in re.findall(r"[a-z]+", aw["name"].lower())):
            continue
        p = doc.add_paragraph()
        _run(p, aw["name"], bold=True)
        if aw.get("detail"):
            _run(p, f". {aw['detail']}")

    secs = {s["entry_id"]: s for s in draft.get("sections", [])}
    order = [s["entry_id"] for s in draft.get("sections", [])]
    for kind, title in (("experience", "Professional Experience"), ("project", "Projects")):
        ids = [i for i in order if profile.entries[i].kind == kind]
        if not ids:
            continue
        _heading(doc, title)
        for eid in ids:
            e = profile.entries[eid]
            if kind == "experience":
                role, company = (e.heading.split(" | ", 1) + [""])[:2]
                _entry_title(doc, f"{role} | {company}", _fmt_dates(e.dates))
            else:
                _entry_title(doc, e.heading, _fmt_dates(e.dates), " • ".join(e.stack), e.link)
            for b in secs[eid]["bullets"]:
                _bullet(doc, b["text"])
    doc.save(path)


def write_letter(text: str, profile: Profile, path: Path):
    doc = _new_doc()
    doc.styles["Normal"].font.size = Pt(11)
    s = doc.sections[0]
    s.left_margin = s.right_margin = Inches(1)
    s.top_margin = s.bottom_margin = Inches(1)
    c = profile.raw["contact"]
    p = doc.add_paragraph()
    _run(p, c["name"], bold=True, size=14)
    _run(doc.add_paragraph(), " • ".join(x for x in [c.get("location"), c.get("phone"), c.get("email")] if x))
    today = date.today()
    _run(doc.add_paragraph(), f"\n{today:%B} {today.day}, {today.year}")
    doc.add_paragraph()
    for para in re.split(r"\n\s*\n", text.strip()):
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(10)
        _run(p, para.strip())
    doc.save(path)


def to_pdf(docx_paths: list[Path]) -> list[Path]:
    """Convert with Microsoft Word (docx2pdf). Returns PDFs created; failures are logged, not raised."""
    made = []
    try:
        from docx2pdf import convert
    except ImportError:
        log.warning("docx2pdf not installed; skipping PDF export")
        return made
    for p in docx_paths:
        pdf = p.with_suffix(".pdf")
        try:
            convert(str(p), str(pdf))
        except Exception as e:  # Word sometimes errors on Quit after a successful export
            if not pdf.exists():
                log.warning("PDF export failed for %s: %s", p.name, e)
        if pdf.exists():
            made.append(pdf)
    return made


def write_all(job: dict, draft: dict, profile: Profile, pdf: bool = True) -> dict:
    d = out_dir_for(job)
    last = _safe(profile.raw["contact"]["name"].split()[-1], 20)
    first = _safe(profile.raw["contact"]["name"].split()[0], 20)
    resume = d / f"{first}_{last}_Resume.docx"
    letter = d / f"{first}_{last}_Cover_Letter.docx"
    write_resume(draft, profile, resume)
    write_letter(draft["cover_letter"], profile, letter)
    (d / "cover_letter.txt").write_text(draft["cover_letter"], encoding="utf-8")
    files = [resume, letter, d / "cover_letter.txt"]
    if pdf:
        files += to_pdf([resume, letter])
    return {"dir": str(d), "files": [str(f) for f in files]}
