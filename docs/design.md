# JobApp: Design (v1)

> This is the original v1 plan, kept for reference. The README describes what was actually built
> (e.g. Claude Code CLI or API backends, an Apps Script bridge instead of a service account, and
> ATS read-back, stats, follow-ups and referral drafts added later).

Goal: make 200+ internship applications manageable. The app finds new postings within minutes, removes duplicates, tailors your resume and cover letter so they pass ATS screening, pre-fills forms, and logs everything to the shared Google Sheet tracker. **Nothing is submitted without your review.**

## Stack
- **Python 3.14 + FastAPI** backend, **SQLite** database (`data/jobapp.db`)
- **Dashboard:** local web page at `http://localhost:8000` (HTMX + Tailwind, no build step)
- **Scheduler:** APScheduler runs inside the app and polls sources every 10 minutes
- **AI:** Claude API (`claude-sonnet-5-5`), with your key stored in `.env` (never committed)
- **Documents:** `python-docx` edits a copy of your real resume `.docx` so its formatting is kept; also exports a PDF
- **Form pre-fill:** Playwright opens a visible Chrome window, fills in the fields, and **stops before Submit**
- **Tracker:** Google Sheets (via a small Apps Script web app) appends and updates rows in a shared application-tracker sheet

## Modules
| Module | What it does |
|---|---|
| `sources/linkedin_email.py` | Reads LinkedIn job-alert emails over IMAP (Gmail/Outlook app password) and extracts the job links |
| `sources/jobspy.py` | JobSpy searches of LinkedIn, Indeed, and Glassdoor (light, rate-limited backup) |
| `sources/ats_boards.py` | Public JSON APIs for Greenhouse, Lever, and Ashby boards at target companies. These are often the earliest place a job appears |
| `sources/jobbank.py` | Job Bank Canada search |
| `dedupe.py` | Normalizes company, title, and location, fuzzy-matches titles, and compares description similarity, so one job posted on 3 sites becomes 1 entry that lists all 3 links |
| `scoring.py` | Ranks each job by fit with your profile (keywords, location, internship/co-op, 2027 dates) |
| `notify.py` | Windows desktop toast for each new job above the score threshold; clicking it opens the job in the dashboard |
| `tailor.py` | Extracts ATS keywords from the posting, then has Claude rewrite your bullets and summary and write a cover letter, **using only facts from your profile** (no made-up experience) |
| `prefill.py` | Playwright fills Greenhouse, Lever, Ashby, and Workday forms and uploads the tailored PDF; you review and click Submit |
| `tracker.py` | Logs to Google Sheet: company, role, link, date found, date applied, status |

## Workflow
1. New job found → deduplicated → scored → **toast notification**
2. Dashboard → "Tailor" → review a side-by-side diff of resume changes, the cover letter, and the ATS keyword coverage score → edit → **Approve**
3. "Pre-fill" → browser opens with the form filled in → **you click Submit** → "Mark applied"
4. Tracker row updated automatically

## Master profile
`profile/master.yaml` holds all of your facts (experience, projects, skills, awards, motivation). Each tailored version picks and rephrases from this file, so every claim traces back to something you provided.

## Build order
1. Skeleton + DB + dashboard + ATS-board and JobSpy sources + dedupe + toast notifications
2. Master profile + resume/cover letter tailoring + review screen
3. Google Sheet tracker sync
4. LinkedIn alert-email parsing
5. Playwright pre-fill
