import sqlite3
from contextlib import contextmanager

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    company_norm TEXT,
    title_norm TEXT,
    location TEXT,
    is_remote INTEGER DEFAULT 0,
    description TEXT,
    posted_at TEXT,
    found_at TEXT NOT NULL,
    score INTEGER DEFAULT 0,
    score_reasons TEXT,
    status TEXT DEFAULT 'new',      -- new | saved | dismissed | applied | interview | offer | rejected
    applied_at TEXT,
    notes TEXT,
    notified INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS job_links (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    url TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    UNIQUE(source, external_id)
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    kind TEXT, started_at TEXT, finished_at TEXT,
    fetched INTEGER, new_jobs INTEGER, duplicates INTEGER, errors TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_found ON jobs(found_at);
CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    status TEXT NOT NULL,            -- running | ready | error | approved
    draft TEXT, scores TEXT, meta TEXT, error TEXT,
    created_at TEXT NOT NULL, updated_at TEXT, approved_at TEXT,
    out_dir TEXT, files TEXT
);
CREATE INDEX IF NOT EXISTS idx_drafts_job ON drafts(job_id);
CREATE TABLE IF NOT EXISTS status_history (
    id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL, status TEXT NOT NULL, at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hist_job ON status_history(job_id);
CREATE TABLE IF NOT EXISTS outreach (
    id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL,
    kind TEXT NOT NULL,              -- followup | referral
    status TEXT NOT NULL,            -- running | ready | error | sent
    data TEXT, error TEXT, created_at TEXT NOT NULL, sent_at TEXT
);
-- Your own reasons for wanting to work at a company, reused for every posting there.
CREATE TABLE IF NOT EXISTS company_notes (
    company_norm TEXT PRIMARY KEY,
    company TEXT NOT NULL,
    why TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

# Columns added after the first release (SQLite can't add them with CREATE TABLE IF NOT EXISTS).
MIGRATIONS = [("drafts", "ats", "TEXT"), ("jobs", "followed_up_at", "TEXT"),
              ("jobs", "followup_snoozed_until", "TEXT")]


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def session():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with session() as c:
        c.executescript(SCHEMA)
        for table, col, typ in MIGRATIONS:
            cols = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
            if col not in cols:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
        # Seed history for jobs that already had a status before history existed.
        c.execute("INSERT INTO status_history (job_id, status, at) "
                  "SELECT id, status, COALESCE(applied_at, found_at) FROM jobs "
                  "WHERE status NOT IN ('new') AND id NOT IN (SELECT job_id FROM status_history)")


def record_status(c, job_id: int, status: str):
    last = c.execute("SELECT status FROM status_history WHERE job_id=? ORDER BY id DESC LIMIT 1",
                     (job_id,)).fetchone()
    if not last or last[0] != status:
        c.execute("INSERT INTO status_history (job_id, status, at) VALUES (?, ?, datetime('now'))",
                  (job_id, status))
