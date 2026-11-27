"""Fetch → filter → dedupe → score → store → notify."""
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import db
from .config import load_config
from .dedupe import find_duplicate
from .notify import notify_jobs
from .scoring import keep, keep_title, score
from .sources import RawJob, boards, jobspy_src
from .text import location_ok, norm_company, norm_title

log = logging.getLogger(__name__)


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _board_jobs(cfg, seen):
    """Yield (RawJob | None, error | None) from every configured company board, 8 boards at a time."""
    b = cfg.get("boards", {})

    def want(title, loc):
        if not keep_title(title, cfg):
            return False
        return not loc or location_ok(loc, False) or "remote" in loc.lower()

    tasks = [(k, e) for k in ("greenhouse", "lever", "ashby", "workday") for e in b.get(k, [])]

    def fetch(kind, e):
        try:
            if kind == "workday":
                gen = boards.workday(c, e["host"], e["tenant"], e["site"], e["company"], want, seen)
            else:
                gen = getattr(boards, kind)(c, e["slug"], e["company"], want, seen)
            return list(gen), None
        except Exception as ex:
            return [], f"{kind}/{e.get('slug') or e.get('site')}: {ex}"[:200]

    with boards.client() as c, ThreadPoolExecutor(8) as pool:
        for jobs, err in pool.map(lambda t: fetch(*t), tasks):
            if err:
                yield None, err
            for j in jobs:
                yield j, None


def _jobspy_jobs(cfg, seen):
    for j in jobspy_src.search(cfg):
        yield j, None
    for e in jobspy_src.search.last_errors:
        yield None, e


def ingest(conn, job: RawJob, cfg: dict) -> tuple[str, dict | None]:
    """Returns ('seen'|'filtered'|'duplicate'|'new', job_row_or_None)."""
    ts = now()
    hit = conn.execute("SELECT id FROM job_links WHERE source=? AND external_id=?",
                       (job.source, job.external_id)).fetchone()
    if hit:
        conn.execute("UPDATE job_links SET last_seen=? WHERE id=?", (ts, hit["id"]))
        return "seen", None
    if job.extra.get("seen_only"):
        return "filtered", None
    if not keep(job, cfg):
        _rejected.add((job.source, job.external_id))
        return "filtered", None

    data = {"title": job.title, "company": job.company, "location": job.location,
            "is_remote": job.is_remote, "description": job.description}
    url = job.url
    dup_id = find_duplicate(conn, data)
    if dup_id:
        conn.execute("INSERT OR IGNORE INTO job_links (job_id, source, external_id, url, first_seen, last_seen)"
                     " VALUES (?,?,?,?,?,?)", (dup_id, job.source, job.external_id, url, ts, ts))
        # Fill in a missing description from the newer copy.
        if job.description:
            conn.execute("UPDATE jobs SET description=? WHERE id=? AND (description IS NULL OR description='')",
                         (job.description, dup_id))
        return "duplicate", None

    pts, why = score(job, cfg)
    cur = conn.execute(
        "INSERT INTO jobs (title, company, company_norm, title_norm, location, is_remote, description,"
        " posted_at, found_at, score, score_reasons) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (job.title, job.company, norm_company(job.company), norm_title(job.title), job.location,
         int(job.is_remote), job.description, job.posted_at, ts, pts, json.dumps(why)))
    jid = cur.lastrowid
    conn.execute("INSERT INTO job_links (job_id, source, external_id, url, first_seen, last_seen)"
                 " VALUES (?,?,?,?,?,?)", (jid, job.source, job.external_id, url, ts, ts))
    return "new", {"id": jid, "title": job.title, "company": job.company,
                   "location": job.location, "score": pts}


_locks = {"boards": threading.Lock(), "jobspy": threading.Lock()}
_rejected: set[tuple[str, str]] = set()  # filtered out after fetching details; skip until restart
_write_lock = threading.Lock()


def run(kind: str = "boards") -> dict:
    """kind: 'boards' or 'jobspy'. Safe to call from the scheduler or the dashboard."""
    lock = _locks[kind]
    if not lock.acquire(blocking=False):
        return {"skipped": f"a {kind} run is already in progress"}
    try:
        cfg = load_config()
        db.init_db()
        stats = {"kind": kind, "fetched": 0, "new": 0, "duplicates": 0, "errors": []}
        started = now()
        new_jobs = []

        # Phase 1: network fetch (slow), no DB lock held.
        with db.session() as r:
            known = {(x[0], x[1]) for x in r.execute("SELECT source, external_id FROM job_links")}

        def seen(source, ext_id):
            return (source, ext_id) in known or (source, ext_id) in _rejected

        gen = _board_jobs(cfg, seen) if kind == "boards" else _jobspy_jobs(cfg, seen)
        fetched = []
        for job, err in gen:
            (stats["errors"].append(err) if err else fetched.append(job))

        # Phase 2: write (fast), one writer at a time.
        with _write_lock, db.session() as conn:
            first_run = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
            for job in fetched:
                stats["fetched"] += 1
                result, row = ingest(conn, job, cfg)
                if result == "new":
                    stats["new"] += 1
                    new_jobs.append(row)
                elif result == "duplicate":
                    stats["duplicates"] += 1

            min_score = cfg["notify"]["min_score"]
            to_notify = [j for j in new_jobs if j["score"] >= min_score]
            # First-ever run imports the backlog quietly instead of firing dozens of pop-ups.
            if new_jobs:
                conn.executemany("UPDATE jobs SET notified=1 WHERE id=?", [(j["id"],) for j in new_jobs])
            conn.execute("INSERT INTO runs (kind, started_at, finished_at, fetched, new_jobs, duplicates, errors)"
                         " VALUES (?,?,?,?,?,?,?)", (kind, started, now(), stats["fetched"], stats["new"],
                                                     stats["duplicates"], json.dumps(stats["errors"])))
        if not first_run:
            notify_jobs(to_notify, cfg["notify"]["max_per_run"])
        log.info("Run %s: %s", kind, {k: v for k, v in stats.items() if k != "errors"})
        if stats["errors"]:
            log.warning("Errors: %s", stats["errors"][:10])
        return stats
    finally:
        lock.release()
