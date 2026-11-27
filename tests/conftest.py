"""Run every test against the fictional example profile, so results don't depend on (or reveal)
the real profile/master.yaml, and the suite passes on a fresh clone."""
import os
from pathlib import Path

os.environ["JOBAPP_PROFILE"] = str(Path(__file__).resolve().parent.parent / "profile" / "master.example.yaml")
