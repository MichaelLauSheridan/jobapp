"""Open a job application in a real browser and pre-fill it. YOU review and click Submit.

Run from the dashboard (draft page -> "Open & pre-fill application"), or:
    .venv\\Scripts\\python -m app.prefill <draft_id>

Hard rules (enforced here, not just intended):
  - never clicks submit/apply/next buttons
  - never types passwords, never solves CAPTCHAs, never creates accounts
  - never ticks consent/terms checkboxes (only self-ID choices you saved in your profile)
  - only fills EMPTY fields, so anything you typed is kept
Uses a separate browser profile (data/browser-profile), so logins you make there (e.g. Workday)
are remembered, and your normal Chrome is untouched.
"""
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from .config import DATA_DIR, ROOT
from .profile import load_profile

PROFILE_DIR = DATA_DIR / "browser-profile"
STATUS_DIR = DATA_DIR / "prefill"
STATUS_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- values
def build_values(profile_raw: dict, files: list[str] | None = None, letter_text: str = "") -> dict:
    c, a, s = profile_raw["contact"], profile_raw.get("application", {}), profile_raw.get("self_id", {})
    first, *rest = c["name"].split()
    files = files or []
    pick = lambda word: next((f for f in files if word in Path(f).name and f.endswith(".pdf")), None) \
        or next((f for f in files if word in Path(f).name and f.endswith(".docx")), None)
    return {
        "first_name": first, "last_name": " ".join(rest), "full_name": c["name"],
        "email": c.get("email", ""), "phone": c.get("phone", ""),
        "linkedin": c.get("linkedin", ""), "github": c.get("github", ""),
        "city": a.get("city", ""), "province": a.get("province", ""), "country": a.get("country", ""),
        "location": c.get("location", ""), "school": a.get("school", ""), "program": a.get("program", ""),
        "degree": a.get("degree", ""), "graduation": a.get("graduation", ""),
        "start_date": a.get("start_date", ""), "how_heard": a.get("how_heard", ""),
        "authorized": a.get("authorized_to_work_in_canada", ""), "sponsorship": a.get("requires_sponsorship", ""),
        "gender": s.get("gender", ""), "ethnicity": s.get("ethnicity", ""),
        "disability": s.get("disability", ""), "veteran": s.get("veteran", ""),
        "cover_letter_text": letter_text,
        "_resume_file": pick("Resume"), "_letter_file": pick("Cover_Letter"),
    }


# ---------------------------------------------------------------- matching
# (pattern on the field's label text, value key). Order matters: specific before general.
RULES = [
    (r"\bpassword\b|captcha|verification code", None),
    (r"preferred (first )?name|nickname", None),
    (r"company|employer|organi[sz]ation|reference|referr(er|al) name|emergency|manager", None),
    (r"first[\s_-]?name|given[\s_-]?name|\bfname\b", "first_name"),
    (r"last[\s_-]?name|family[\s_-]?name|surname|\blname\b", "last_name"),
    (r"e-?mail", "email"),
    (r"phone|mobile|\btel\b|cell", "phone"),
    (r"linked\s?in", "linkedin"),
    (r"github", "github"),
    (r"cover[\s_-]?letter", "cover_letter_text"),
    (r"\bcity\b|town", "city"),
    (r"province|\bstate\b|region", "province"),
    (r"\bcountry\b", "country"),
    (r"authori[sz]ed to work|eligible to work|legally (entitled|able|permitted|allowed) to work|work authori[sz]ation|right to work", "authorized"),
    (r"sponsor", "sponsorship"),
    (r"gender|\bsex\b", "gender"),
    (r"ethnic|\brace\b|racial|visible minorit", "ethnicity"),
    (r"disabilit", "disability"),
    (r"veteran", "veteran"),
    (r"how did you (hear|find|learn)|where did you (hear|find|see)|referral source|\bsource\b", "how_heard"),
    (r"start date|available to start|earliest (start|availability)|when can you start", "start_date"),
    (r"graduat", "graduation"),
    (r"school|universit|college|institution", "school"),
    (r"degree|program|field of study|\bmajor\b|discipline", "program"),
    (r"full[\s_-]?name|legal name|your name|^name\b|\bname$|\bname\s*\*?$", "full_name"),
    (r"location|where are you (based|located)|current address", "location"),
]
US_ONLY = re.compile(r"united states|\bu\.?s\.?a?\b|america", re.I)

# Candidate option texts for choice fields (regex, tried in order).
CHOICES = {
    "authorized": [r"^\s*yes\b"],
    "sponsorship": [r"^\s*no\b"],
    "gender": [r"(?<!fe)\bmale\b", r"\bman\b"],
    "ethnicity": [r"east asian", r"chinese", r"\basian\b"],
    "disability": [r"^\s*no\b", r"do(n't| not) have", r"not have a disability"],
    "veteran": [r"not a (protected )?veteran", r"i am not", r"^\s*no\b"],
    "province": [r"^ontario$", r"^on$", r"ontario"],
    "country": [r"^canada$", r"canada"],
    "how_heard": [r"company (website|site|career)", r"career(s)? (site|page|website)", r"website", r"job board", r"^other$"],
    "program": [r"computer science", r"bachelor"],
    "school": [r"sheridan"],
}


def classify(direct: str, context: str) -> str | None:
    for text in (direct, context):
        t = (text or "").lower()
        for pat, key in RULES:
            if re.search(pat, t):
                return key
    return None


def choose(options: list[str], key: str, value: str) -> str | None:
    pats = CHOICES.get(key) or [re.escape(value.lower())]
    for pat in pats:
        for o in options:
            if o and re.search(pat, o.lower()):
                return o
    return None


# ---------------------------------------------------------------- page side
COLLECT_JS = r"""
() => {
  const out = []; let i = 0;
  const vis = el => { const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const txt = e => (e && (e.innerText || e.textContent) || '').replace(/\s+/g, ' ').trim();
  document.querySelectorAll('[data-jobapp-i]').forEach(e => e.removeAttribute('data-jobapp-i'));
  document.querySelectorAll('input, textarea, select').forEach(el => {
    if (el.closest('#jobapp-panel-host')) return;
    const type = (el.type || el.tagName).toLowerCase();
    if (['hidden','submit','button','reset','image','password','search'].includes(type)) return;
    if (type !== 'file' && !vis(el)) return;
    const direct = [];
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) direct.push(txt(l)); }
    const lb = el.getAttribute('aria-labelledby');
    if (lb) lb.split(/\s+/).forEach(id => { const e = document.getElementById(id); if (e) direct.push(txt(e)); });
    ['aria-label','placeholder','name','id','autocomplete','data-qa'].forEach(a => { const v = el.getAttribute(a); if (v) direct.push(v); });
    let context = '';
    if (type === 'radio' || type === 'checkbox') {
      let p = el.parentElement;
      const sel = `input[type=${type}][name="${CSS.escape(el.name || '')}"]`;
      while (p && el.name && p.querySelectorAll(sel).length < 2) p = p.parentElement;
      const fs = el.closest('fieldset'); const lg = fs && fs.querySelector('legend');
      context = (lg ? txt(lg) + ' | ' : '') + (p ? txt(p).slice(0, 400) : '');
    } else {
      const pl = el.closest('label'); if (pl) direct.push(txt(pl));
      let p = el.parentElement;
      for (let k = 0; k < 4 && p; k++, p = p.parentElement) {
        const t = txt(p); if (t && t.length < 300) { context = t; break; }
      }
    }
    let optLabel = null;
    if (type === 'radio' || type === 'checkbox') {
      const l = el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      optLabel = txt(l) || txt(el.closest('label')) || el.value || '';
    }
    el.setAttribute('data-jobapp-i', i);
    out.push({ i, tag: el.tagName.toLowerCase(), type, name: el.name || '', direct: direct.join(' | '),
      context, required: el.required || el.getAttribute('aria-required') === 'true',
      value: (type === 'checkbox' || type === 'radio') ? el.checked : (el.value || ''),
      options: el.tagName === 'SELECT' ? [...el.options].map(o => o.text.trim()) : null,
      optLabel, role: el.getAttribute('role') || '' });
    i++;
  });
  return out;
}
"""

TEXT_TYPES = {"text", "email", "tel", "url", "textarea", "number"}


def fill_frame(frame, values: dict, report: dict):
    try:
        fields = frame.evaluate(COLLECT_JS)
    except Exception:
        return
    loc = lambda f: frame.locator(f'[data-jobapp-i="{f["i"]}"]')
    done_groups = set()
    for f in fields:
        label = (f["direct"] or f["context"])[:80]
        key = classify(f["direct"], f["context"])
        try:
            if f["type"] == "file":
                ctx = (f["direct"] + " " + f["context"]).lower()
                path = values["_letter_file"] if "cover" in ctx else values["_resume_file"]
                if path and not report.get(f"file:{path}"):
                    loc(f).set_input_files(path)
                    report[f"file:{path}"] = True
                    report["filled"].append(f"{'Cover letter' if 'cover' in ctx else 'Resume'} file")
                continue
            if not key or not values.get(key):
                if f["required"] and not f["value"] and f["type"] not in ("checkbox",):
                    report["needs_you"].append(label or f["name"])
                continue
            val = values[key]
            if key in ("authorized", "sponsorship") and US_ONLY.search(f["direct"] + " " + f["context"]):
                report["needs_you"].append(label + " (asks about the U.S.)")
                continue
            if f["tag"] == "select":
                if f["value"] and f["options"] and f["value"] not in ("", f["options"][0]):
                    continue  # already chosen
                opt = choose(f["options"] or [], key, val)
                if opt:
                    loc(f).select_option(label=opt)
                    report["filled"].append(f"{label}: {opt}")
                elif f["required"]:
                    report["needs_you"].append(label)
            elif f["type"] == "radio":
                if f["name"] in done_groups:
                    continue
                group = [g for g in fields if g["type"] == "radio" and g["name"] == f["name"]]
                if any(g["value"] for g in group):
                    done_groups.add(f["name"]); continue
                opt = choose([g["optLabel"] for g in group], key, val)
                if opt:
                    target = next(g for g in group if g["optLabel"] == opt)
                    loc(target).check()
                    report["filled"].append(f"{label}: {opt}")
                done_groups.add(f["name"])
            elif f["type"] == "checkbox":
                # Only self-ID choices you saved (e.g. ethnicity boxes). Never consent/terms boxes.
                if key in ("gender", "ethnicity", "disability", "veteran") and not f["value"] \
                        and choose([f["optLabel"]], key, val) and f["name"] not in done_groups:
                    loc(f).check()
                    done_groups.add(f["name"])
                    report["filled"].append(f"{label}: {f['optLabel']}")
            elif f["role"] == "combobox":
                if not f["value"]:
                    el = loc(f)
                    el.click(); el.type(val.split(" (")[0], delay=20)
                    frame.wait_for_timeout(700)
                    opts = frame.locator('[role="option"]')
                    texts = [opts.nth(k).inner_text() for k in range(min(opts.count(), 30))]
                    opt = choose(texts, key, val)
                    if opt:
                        opts.nth(texts.index(opt)).click()
                        report["filled"].append(f"{label}: {opt}")
                    else:
                        el.press("Escape")
            elif f["type"] in TEXT_TYPES and not f["value"]:
                if key == "cover_letter_text" and f["tag"] != "textarea":
                    continue
                loc(f).fill(val)
                report["filled"].append(f"{label}: {val[:40]}{'…' if len(val) > 40 else ''}")
        except Exception as e:
            report["errors"].append(f"{label}: {str(e).splitlines()[0][:100]}")


def fill_page(page, values: dict) -> dict:
    report = {"filled": [], "needs_you": [], "errors": [], "url": page.url, "at": datetime.now().isoformat(timespec="seconds")}
    for frame in page.frames:
        fill_frame(frame, values, report)
    report["needs_you"] = list(dict.fromkeys(x for x in report["needs_you"] if x))
    return report


# ---------------------------------------------------------------- helper panel
def panel_js(values: dict) -> str:
    items = [("First name", "first_name"), ("Last name", "last_name"), ("Email", "email"), ("Phone", "phone"),
             ("LinkedIn", "linkedin"), ("GitHub", "github"), ("City", "city"), ("School", "school"),
             ("Program", "program"), ("Graduation", "graduation"), ("Start date", "start_date"),
             ("Cover letter text", "cover_letter_text")]
    data = [{"label": l, "value": values.get(k, "")} for l, k in items if values.get(k)]
    files = [Path(p).name for p in (values.get("_resume_file"), values.get("_letter_file")) if p]
    return """
(() => {
  if (window.top !== window) return;
  const DATA = %s, FILES = %s;
  const build = () => {
    if (document.getElementById('jobapp-panel-host') || !document.body) return;
    const host = document.createElement('div'); host.id = 'jobapp-panel-host';
    host.style.cssText = 'position:fixed;right:12px;bottom:12px;z-index:2147483647;';
    const root = host.attachShadow({mode: 'open'});
    root.innerHTML = `<style>
      .p{font:13px system-ui,sans-serif;background:#fff;color:#1e293b;border:1px solid #cbd5e1;border-radius:10px;
         box-shadow:0 6px 24px rgba(0,0,0,.18);width:290px;max-height:70vh;overflow:auto}
      .h{background:#1e293b;color:#fff;padding:8px 10px;display:flex;justify-content:space-between;align-items:center;
         border-radius:10px 10px 0 0;cursor:pointer}
      .b{padding:8px 10px} button{font:12px system-ui;border:1px solid #cbd5e1;background:#f8fafc;border-radius:6px;
         padding:3px 7px;cursor:pointer} .row{display:flex;justify-content:space-between;gap:6px;margin:4px 0;align-items:center}
      .v{color:#64748b;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:150px}
      .s{font-size:12px;color:#334155;margin:6px 0;white-space:pre-line} .warn{color:#b45309}
      .go{background:#1d4ed8;color:#fff;border-color:#1d4ed8;width:100%%;padding:6px;margin:4px 0}
    </style>
    <div class="p"><div class="h"><b>JobApp</b><span id="t">–</span></div><div class="b" id="body">
      <div class="s" id="st">Filling…</div>
      <button class="go" id="fill">Fill this page again</button>
      <div class="s warn">Review everything, then click Submit yourself.</div>
      <div id="rows"></div>
      <div class="s">Files to attach (already chosen where possible):<br>${FILES.join('<br>') || '—'}</div>
    </div></div>`;
    const rows = root.getElementById('rows');
    DATA.forEach(d => {
      const r = document.createElement('div'); r.className = 'row';
      r.innerHTML = `<span><b>${d.label}</b><br><span class="v"></span></span><button>Copy</button>`;
      r.querySelector('.v').textContent = d.value;
      r.querySelector('button').onclick = async (e) => { await navigator.clipboard.writeText(d.value);
        e.target.textContent = 'Copied'; setTimeout(() => e.target.textContent = 'Copy', 1200); };
      rows.appendChild(r);
    });
    root.getElementById('fill').onclick = () => { window.__jobappFillRequested = Date.now();
      root.getElementById('st').textContent = 'Filling…'; };
    root.querySelector('.h').onclick = () => { const b = root.getElementById('body');
      b.style.display = b.style.display === 'none' ? '' : 'none'; };
    window.__jobappSetStatus = (s) => { root.getElementById('st').textContent = s; };
    document.documentElement.appendChild(host);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', build); else build();
  setInterval(build, 1500);
})();
""" % (json.dumps(data), json.dumps(files))


def status_text(r: dict) -> str:
    s = f"Filled {len(r['filled'])} field(s)."
    if r["needs_you"]:
        s += f"\nNeeds you ({len(r['needs_you'])}): " + "; ".join(r["needs_you"][:6])
    return s


# ---------------------------------------------------------------- run
def apply_url(links: list[dict]) -> str:
    """Best place to apply: the company's own form (straight to the form when we know how)."""
    order = ("greenhouse", "lever", "ashby", "workday")
    best = sorted(links, key=lambda l: order.index(l["source"]) if l["source"] in order else 9)
    if not best:
        return ""
    l = best[0]
    if l["source"] == "greenhouse" and ":" in l["external_id"]:
        slug, jid = l["external_id"].split(":", 1)
        return f"https://job-boards.greenhouse.io/{slug}/jobs/{jid}"
    if l["source"] == "lever" and not l["url"].rstrip("/").endswith("/apply"):
        return l["url"].rstrip("/") + "/apply"
    if l["source"] == "ashby" and not l["url"].rstrip("/").endswith("/application"):
        return l["url"].rstrip("/") + "/application"
    return l["url"]


def write_status(draft_id: int, data: dict):
    (STATUS_DIR / f"{draft_id}.json").write_text(json.dumps(data, indent=1), encoding="utf-8")


def run(draft_id: int, channel: str = "chrome"):
    from playwright.sync_api import sync_playwright

    from . import db
    with db.session() as c:
        d = c.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
        links = [dict(r) for r in c.execute("SELECT * FROM job_links WHERE job_id=?", (d["job_id"],))]
    files = json.loads(d["files"] or "[]")
    letter = json.loads(d["draft"]).get("cover_letter", "")
    values = build_values(load_profile().raw, files, letter)
    url = apply_url(links)
    status = {"draft_id": draft_id, "state": "open", "url": url, "pid": __import__("os").getpid(), "fills": []}
    write_status(draft_id, status)
    try:
        with sync_playwright() as p:
            try:
                ctx = p.chromium.launch_persistent_context(str(PROFILE_DIR), channel=channel, headless=False,
                                                           no_viewport=True, args=["--start-maximized"])
            except Exception:
                ctx = p.chromium.launch_persistent_context(str(PROFILE_DIR), channel="msedge", headless=False,
                                                           no_viewport=True, args=["--start-maximized"])
            ctx.add_init_script(panel_js(values))
            closed = {"v": False}
            ctx.on("close", lambda *_: closed.update(v=True))
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(url, wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass

            def do_fill(pg):
                r = fill_page(pg, values)
                status["fills"].append(r)
                write_status(draft_id, status)
                try:
                    pg.evaluate("s => window.__jobappSetStatus && window.__jobappSetStatus(s)", status_text(r))
                except Exception:
                    pass

            do_fill(page)
            last = {}
            while not closed["v"] and ctx.pages:
                for pg in list(ctx.pages):
                    try:
                        req = pg.evaluate("window.__jobappFillRequested || 0")
                    except Exception:
                        continue
                    if req and req != last.get(id(pg)):
                        last[id(pg)] = req
                        do_fill(pg)
                try:
                    ctx.pages[0].wait_for_timeout(700)
                except Exception:
                    break
    finally:
        status["state"] = "closed"
        write_status(draft_id, status)


def _alive(pid: int) -> bool:
    """Windows-safe process check (os.kill(pid, 0) would send Ctrl+C on Windows)."""
    import ctypes
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = ctypes.c_ulong()
    k.GetExitCodeProcess(h, ctypes.byref(code))
    k.CloseHandle(h)
    return code.value == 259  # STILL_ACTIVE


def read_status(draft_id: int) -> dict | None:
    p = STATUS_DIR / f"{draft_id}.json"
    if not p.exists():
        return None
    st = json.loads(p.read_text(encoding="utf-8"))
    if st.get("state") == "open" and not _alive(st.get("pid", 0)):
        st["state"] = "closed"
    return st


if __name__ == "__main__":
    run(int(sys.argv[1]))
