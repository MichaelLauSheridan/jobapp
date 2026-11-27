from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "jobapp.db"


def load_config() -> dict:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    boards_file = ROOT / "boards.yaml"
    cfg["boards"] = {}
    if boards_file.exists():
        with open(boards_file, encoding="utf-8") as f:
            cfg["boards"] = yaml.safe_load(f) or {}
    return cfg
