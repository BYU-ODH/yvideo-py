from pathlib import Path
import sqlite3
import tempfile

from core.legacy_migration.bulk_import import REQUIRED_COLUMNS


def make_legacy_source(test_case):
    """A legacy sqlite database with one user, jdoe (u1), who owns one collection (c1)."""
    tmp = tempfile.TemporaryDirectory()
    test_case.addCleanup(tmp.cleanup)
    source = Path(tmp.name) / "legacy.sqlite"
    with sqlite3.connect(source) as db:
        for table, columns in REQUIRED_COLUMNS.items():
            names = ["id", "deleted", "created", "updated", *sorted(columns)]
            db.execute(f'CREATE TABLE "{table}" ({", ".join(names)})')
        db.execute(
            "INSERT INTO users (id, username, byu_person_id, email, account_name, account_type)"
            " VALUES ('u1', 'jdoe', '999888777', '', 'Jane Doe', 1)"
        )
        db.execute(
            "INSERT INTO collections (id, collection_name, owner, published, archived)"
            " VALUES ('c1', 'Spanish 101', 'u1', 'true', 'false')"
        )
    return source
