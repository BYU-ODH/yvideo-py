from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SOURCE_DATABASE = {
    "dbname": "",
    "user": "",
    "password": "",
    "host": "",
    "port": 5432,
    "sslmode": "prefer",
}

SOURCE_SCHEMA = "public"
TARGET_SQLITE_PATH = REPO_ROOT / "var" / "legacy_migration" / "legacy_dump.sqlite3"
BATCH_SIZE = 2000
