"""Project-relative paths, shared so every module resolves .env and data/
against the project root regardless of where it's run from."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / '.env'
DATA_DIR = PROJECT_ROOT / 'data'

DATA_DIR.mkdir(exist_ok=True)
