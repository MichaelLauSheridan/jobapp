@echo off
REM Start JobApp: dashboard at http://localhost:8000 + background job polling.
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist .venv (
  echo First run: setting up Python environment...
  python -m venv .venv && .venv\Scripts\python -m pip install -r requirements.txt
)
if not exist boards.yaml .venv\Scripts\python scripts\find_boards.py
start "" http://localhost:8000
.venv\Scripts\python -m app.main
