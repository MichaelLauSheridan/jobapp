"""Your reasons for wanting to work at a specific company ("Why BMO?"), in your own words.

Saved once per company and reused for every posting there, so cover letters and messages can say
why this company instead of asking you with a [NEEDS: …] placeholder each time."""
from datetime import datetime, timezone

from . import db
from .text import norm_company


def get_why(company: str) -> str:
    with db.session() as c:
        row = c.execute("SELECT why FROM company_notes WHERE company_norm=?", (norm_company(company),)).fetchone()
    return row["why"] if row else ""


def set_why(company: str, why: str):
    why = why.replace("\r\n", "\n").strip()
    key = norm_company(company)
    with db.session() as c:
        if not why:
            c.execute("DELETE FROM company_notes WHERE company_norm=?", (key,))
            return
        c.execute("INSERT INTO company_notes (company_norm, company, why, updated_at) VALUES (?,?,?,?)"
                  " ON CONFLICT(company_norm) DO UPDATE SET why=excluded.why, company=excluded.company,"
                  " updated_at=excluded.updated_at",
                  (key, company, why, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")))


def prompt_block(company: str) -> str:
    """Text for the AI prompt. Empty when you haven't written anything for this company."""
    why = get_why(company)
    if not why:
        return ""
    return (f"CANDIDATE'S REASONS FOR WANTING TO WORK AT {company.upper()} (their own words; you may use "
            f"these as motivation, rephrased but never extended with new claims):\n{why}\n\n")
