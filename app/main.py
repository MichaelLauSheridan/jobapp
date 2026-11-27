"""JobApp dashboard + background polling. Run with:  run.bat  (or)  python -m app.main"""
import json
import logging
import os
import threading
from urllib.parse import quote
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from . import db, drafting, pipeline, tracker
from .config import ROOT, load_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("jobapp")
templates = Jinja2Templates(directory=str(ROOT / "app" / "templates"))
scheduler = BackgroundScheduler()

STATUSES = ["new", "saved", "ready", "applied", "interview", "offer", "rejected", "dismissed"]
STATUS_LABELS = {"ready": "docs ready"}
SOURCE_LABELS = {"greenhouse": "Company site (Greenhouse)", "lever": "Company site (Lever)",
                 "ashby": "Company site (Ashby)", "workday": "Company site (Workday)",
                 "linkedin": "LinkedIn", "indeed": "Indeed", "glassdoor": "Glassdoor"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    cfg = load_config()
    pm = cfg["poll_minutes"]
    now = datetime.now()
    scheduler.add_job(pipeline.run, "interval", args=["boards"], minutes=pm["boards"],
                      next_run_time=now, id="boards", max_instances=1, coalesce=True)
    scheduler.add_job(pipeline.run, "interval", args=["jobspy"], minutes=pm["jobspy"],
                      next_run_time=now, id="jobspy", max_instances=1, coalesce=True)
    scheduler.add_job(tracker.flush, "interval", minutes=5, id="tracker", max_instances=1, coalesce=True)
    from . import followups
    scheduler.add_job(followups.daily_reminder, "interval", minutes=60, next_run_time=now,
                      id="followups", max_instances=1, coalesce=True)
    scheduler.start()
    log.info("Dashboard: http://localhost:8000  (boards every %sm, LinkedIn/Indeed every %sm)",
             pm["boards"], pm["jobspy"])
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(lifespan=lifespan)


def _ago(ts: str | None) -> str:
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return str(ts)[:10]
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    mins = int((datetime.now(timezone.utc) - dt).total_seconds() // 60)
    if mins < 60:
        return f"{max(mins, 0)}m ago"
    if mins < 60 * 48:
        return f"{mins // 60}h ago"
    return f"{mins // 1440}d ago"


templates.env.filters["ago"] = _ago


@app.get("/", response_class=HTMLResponse)
def index(request: Request, status: str = "active", min_score: int = 0, q: str = "", sort: str = "new"):
    where, args = ["score >= ?"], [min_score]
    if status == "active":
        where.append("status IN ('new','saved','ready')")
    elif status != "all":
        where.append("status = ?"); args.append(status)
    if q:
        where.append("(title LIKE ? OR company LIKE ?)"); args += [f"%{q}%", f"%{q}%"]
    order = "score DESC, found_at DESC" if sort == "score" else "found_at DESC, score DESC"
    with db.session() as c:
        jobs = c.execute(
            f"SELECT j.*, (SELECT COUNT(*) FROM job_links l WHERE l.job_id=j.id) AS n_links,"
            f" (SELECT GROUP_CONCAT(DISTINCT source) FROM job_links l WHERE l.job_id=j.id) AS sources"
            f" FROM jobs j WHERE {' AND '.join(where)} ORDER BY {order} LIMIT 500", args).fetchall()
        counts = dict(c.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall())
        runs = c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 6").fetchall()
    return templates.TemplateResponse(request, "index.html", {
        "jobs": jobs, "counts": counts, "runs": runs, "status": status, "min_score": min_score,
        "q": q, "sort": sort, "statuses": STATUSES, "json": json,
        "running": [k for k, l in pipeline._locks.items() if l.locked()]})


@app.get("/job/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: int):
    with db.session() as c:
        job = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            raise HTTPException(404)
        links = c.execute("SELECT * FROM job_links WHERE job_id=? ORDER BY first_seen", (job_id,)).fetchall()
    return templates.TemplateResponse(request, "job.html", {
        "job": job, "links": links, "reasons": json.loads(job["score_reasons"] or "[]"),
        "statuses": STATUSES, "labels": SOURCE_LABELS, "draft": drafting.latest(job_id),
        "tailor_cfg": load_config().get("tailor", {}),
        "out": __import__("app.outreach", fromlist=["latest"]).latest(job_id),
        "searches": __import__("app.outreach", fromlist=["x"]).linkedin_searches(job["company"], job["title"])})


@app.post("/job/{job_id}/tailor")
def start_tailor(job_id: int):
    drafting.start(job_id)
    return RedirectResponse(f"/job/{job_id}/draft", status_code=303)


@app.get("/job/{job_id}/draft", response_class=HTMLResponse)
def view_draft(request: Request, job_id: int, msg: str = "", err: str = ""):
    from .profile import load_profile

    with db.session() as c:
        job = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    d = drafting.latest(job_id)
    if not job or not d:
        return RedirectResponse(f"/job/{job_id}", status_code=303)
    profile = load_profile()
    return templates.TemplateResponse(request, "draft.html", {
        "job": job, "d": d, "entries": profile.entries, "msg": msg, "err": err,
        "needs": drafting.open_needs(d["draft"]) if d["draft"] else [],
        "prefill": __import__("app.prefill", fromlist=["read_status"]).read_status(d["id"]),
        "file_names": [os.path.basename(f) for f in (d["files"] or [])]})


@app.post("/draft/{draft_id}/save")
async def save_draft(request: Request, draft_id: int):
    form = dict(await request.form())
    job_id = int(form.pop("job_id"))
    action = form.pop("action", "save")
    drafting.apply_edits(draft_id, form)
    if action == "approve":
        try:
            drafting.approve(draft_id)
        except ValueError as e:
            return RedirectResponse(f"/job/{job_id}/draft?err={quote(str(e))}", status_code=303)
        return RedirectResponse(f"/job/{job_id}/draft?msg={quote('Files created.')}", status_code=303)
    return RedirectResponse(f"/job/{job_id}/draft?msg={quote('Saved and re-scored.')}", status_code=303)


@app.get("/draft/{draft_id}/file/{name}")
def draft_file(draft_id: int, name: str):
    with db.session() as c:
        r = c.execute("SELECT files FROM drafts WHERE id=?", (draft_id,)).fetchone()
    for f in json.loads(r["files"] or "[]") if r else []:
        if os.path.basename(f) == name and os.path.exists(f):
            return FileResponse(f, filename=name)
    raise HTTPException(404)


@app.post("/draft/{draft_id}/prefill")
def start_prefill(draft_id: int):
    import subprocess
    import sys

    from . import prefill
    with db.session() as c:
        r = c.execute("SELECT job_id, status FROM drafts WHERE id=?", (draft_id,)).fetchone()
    if not r:
        raise HTTPException(404)
    if r["status"] != "approved":
        return RedirectResponse(f"/job/{r['job_id']}/draft?err={quote('Approve the draft first so the files exist.')}",
                                status_code=303)
    st = prefill.read_status(draft_id)
    if not (st and st.get("state") == "open"):
        subprocess.Popen([sys.executable, "-m", "app.prefill", str(draft_id)], cwd=str(ROOT),
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return RedirectResponse(f"/job/{r['job_id']}/draft?msg={quote('Opening the application in a browser window…')}",
                            status_code=303)


@app.post("/draft/{draft_id}/open-folder")
def open_folder(draft_id: int):
    with db.session() as c:
        r = c.execute("SELECT job_id, out_dir FROM drafts WHERE id=?", (draft_id,)).fetchone()
    if r and r["out_dir"] and os.path.isdir(r["out_dir"]):
        os.startfile(r["out_dir"])  # Windows Explorer
    return RedirectResponse(f"/job/{r['job_id']}/draft" if r else "/", status_code=303)


@app.post("/job/{job_id}/status")
def set_status(job_id: int, status: str = Form(...), notes: str = Form(None), back: str = Form("/")):
    if status not in STATUSES:
        raise HTTPException(400)
    with db.session() as c:
        c.execute("UPDATE jobs SET status=?, applied_at=CASE WHEN ?='applied' AND applied_at IS NULL"
                  " THEN datetime('now') ELSE applied_at END WHERE id=?", (status, status, job_id))
        db.record_status(c, job_id, status)
        if notes is not None:
            c.execute("UPDATE jobs SET notes=? WHERE id=?", (notes, job_id))
    tracker.enqueue(job_id)
    return RedirectResponse(back if back.startswith("/") else "/", status_code=303)


@app.get("/followups", response_class=HTMLResponse)
def followups_page(request: Request, msg: str = ""):
    from . import followups, outreach
    data = followups.lists()
    drafts = {j["id"]: outreach.latest(j["id"]).get("followup") for j in data["due"] + data["upcoming"]}
    running = any(d and d["status"] == "running" for d in drafts.values())
    return templates.TemplateResponse(request, "followups.html",
                                      {"f": data, "drafts": drafts, "msg": msg, "running": running})


@app.post("/job/{job_id}/outreach/{kind}")
def outreach_start(job_id: int, kind: str, back: str = Form("")):
    from . import outreach
    if kind not in ("followup", "referral"):
        raise HTTPException(400)
    outreach.start(job_id, kind)
    return RedirectResponse(back if back.startswith("/") else f"/job/{job_id}#outreach", status_code=303)


@app.post("/outreach/{oid}/sent")
def outreach_sent(oid: int, back: str = Form("")):
    from . import outreach
    job_id = outreach.mark_sent(oid)
    return RedirectResponse(back if back.startswith("/") else f"/job/{job_id}#outreach", status_code=303)


@app.post("/job/{job_id}/followup/{action}")
def followup_action(job_id: int, action: str):
    from . import followups
    if action == "snooze":
        followups.snooze(job_id, 3)
    elif action == "done":
        followups.mark_done(job_id)
    else:
        raise HTTPException(400)
    return RedirectResponse("/followups", status_code=303)


@app.get("/stats", response_class=HTMLResponse)
def stats_page(request: Request):
    from . import stats
    return templates.TemplateResponse(request, "stats.html", {"s": stats.compute()})


@app.get("/tracker", response_class=HTMLResponse)
def tracker_page(request: Request, test: int = 0, msg: str = ""):
    url, _ = tracker.credentials()
    result = tracker.test_connection() if (test and url) else None
    mapping = tracker.check_mapping(result.get("headers", [])) if result and result.get("ok") else None
    with db.session() as c:
        c.executescript(tracker.OUTBOX)
        recent = c.execute("SELECT o.*, j.company, j.title FROM tracker_outbox o JOIN jobs j ON j.id=o.job_id "
                           "ORDER BY o.id DESC LIMIT 15").fetchall()
    return templates.TemplateResponse(request, "tracker.html", {
        "url": url, "script": tracker.script_text(), "result": result, "mapping": mapping,
        "cfg": tracker.cfg(), "recent": recent, "msg": msg})


@app.post("/tracker/url")
def tracker_save_url(url: str = Form(...)):
    tracker.save_url(url)
    return RedirectResponse("/tracker?test=1", status_code=303)


@app.post("/tracker/sync-all")
def tracker_sync_all():
    statuses = tracker.cfg().get("sync_statuses", [])
    with db.session() as c:
        ids = [r[0] for r in c.execute(
            f"SELECT id FROM jobs WHERE status IN ({','.join('?' * len(statuses))})", statuses)] if statuses else []
    for i in ids:
        tracker.enqueue(i)
    return RedirectResponse(f"/tracker?msg={quote(f'Queued {len(ids)} job(s) for the sheet.')}", status_code=303)


@app.post("/run/{kind}")
def run_now(kind: str):
    if kind not in ("boards", "jobspy"):
        raise HTTPException(400)
    threading.Thread(target=pipeline.run, args=[kind], daemon=True).start()
    return RedirectResponse("/", status_code=303)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
