#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "psycopg[binary]>=3.2,<4",
# ]
# ///

import datetime
import decimal
import importlib.util
import json
from pathlib import Path
import sqlite3
from uuid import UUID

import psycopg

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

REPO_ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = Path(__file__).with_name("dump_legacy_to_sqlite_settings.py")

LEGACY_TABLES = [
    "users",
    "collections",
    "user_collections_assoc",
    "courses",
    "collection_courses_assoc",
    "resources",
    "resource_access",
    "files",
    "contents",
    "subtitles",
]

INDEX_STATEMENTS = {
    "users": [
        "CREATE INDEX IF NOT EXISTS idx_users_username ON users (username)",
    ],
    "collections": [
        "CREATE INDEX IF NOT EXISTS idx_collections_owner ON collections (owner)",
    ],
    "user_collections_assoc": [
        "CREATE INDEX IF NOT EXISTS idx_uca_collection_id ON user_collections_assoc (collection_id)",
        "CREATE INDEX IF NOT EXISTS idx_uca_username ON user_collections_assoc (username)",
    ],
    "collection_courses_assoc": [
        "CREATE INDEX IF NOT EXISTS idx_cca_collection_id ON collection_courses_assoc (collection_id)",
    ],
    "resources": [
        "CREATE INDEX IF NOT EXISTS idx_resources_name ON resources (resource_name)",
    ],
    "resource_access": [
        "CREATE INDEX IF NOT EXISTS idx_resource_access_resource_id ON resource_access (resource_id)",
        "CREATE INDEX IF NOT EXISTS idx_resource_access_username ON resource_access (username)",
    ],
    "files": [
        "CREATE INDEX IF NOT EXISTS idx_files_resource_version ON files (resource_id, file_version)",
    ],
    "contents": [
        "CREATE INDEX IF NOT EXISTS idx_contents_collection_id ON contents (collection_id)",
        "CREATE INDEX IF NOT EXISTS idx_contents_resource_version ON contents (resource_id, file_version)",
    ],
    "subtitles": [
        "CREATE INDEX IF NOT EXISTS idx_subtitles_content_id ON subtitles (content_id)",
    ],
}


def load_local_settings():
    if not SETTINGS_PATH.exists():
        raise SystemExit(
            "Missing scripts/dump_legacy_to_sqlite_settings.py. Copy "
            "scripts/dump_legacy_to_sqlite_settings_template.py and fill in the legacy database credentials."
        )

    spec = importlib.util.spec_from_file_location(
        "dump_legacy_to_sqlite_settings", SETTINGS_PATH
    )
    if spec is None or spec.loader is None:
        raise SystemExit(f"Could not load settings from {SETTINGS_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sqlite_type_for_column(column):
    data_type = (column["data_type"] or "").lower()
    udt_name = (column["udt_name"] or "").lower()

    if data_type in {"smallint", "integer", "bigint"}:
        return "INTEGER"
    if data_type in {"boolean"}:
        return "INTEGER"
    if data_type in {"real", "double precision", "numeric", "decimal"}:
        return "REAL"
    if data_type in {"bytea"}:
        return "BLOB"
    if udt_name in {"int2", "int4", "int8", "bool"}:
        return "INTEGER"
    if udt_name in {"float4", "float8", "numeric"}:
        return "REAL"
    return "TEXT"


def normalize_value(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, datetime.datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, (datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, memoryview):
        return bytes(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    return value


def open_lock(lock_path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(lock_path, "w")
    if fcntl is None:
        return lock_file
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        lock_file.close()
        raise SystemExit("Legacy dump is already running in another process.") from exc
    return lock_file


def table_columns(pg_conn, schema_name, table_name):
    query = """
        SELECT
            c.column_name,
            c.data_type,
            c.udt_name
        FROM information_schema.columns c
        WHERE c.table_schema = %s
          AND c.table_name = %s
        ORDER BY c.ordinal_position
    """
    with pg_conn.cursor() as cursor:
        cursor.execute(query, [schema_name, table_name])
        return [
            {
                "column_name": row[0],
                "data_type": row[1],
                "udt_name": row[2],
            }
            for row in cursor.fetchall()
        ]


def create_sqlite_table(sqlite_conn, table_name, columns):
    definitions = [
        f'"{column["column_name"]}" {sqlite_type_for_column(column)}'
        for column in columns
    ]
    sqlite_conn.execute(f'DROP TABLE IF EXISTS "{table_name}"')
    sqlite_conn.execute(f'CREATE TABLE "{table_name}" ({", ".join(definitions)})')


def copy_table(pg_conn, sqlite_conn, schema_name, table_name, batch_size):
    columns = table_columns(pg_conn, schema_name, table_name)
    if not columns:
        raise RuntimeError(
            f"Legacy table {schema_name}.{table_name} was not found or has no columns."
        )

    create_sqlite_table(sqlite_conn, table_name, columns)

    column_names = [column["column_name"] for column in columns]
    quoted_columns = ", ".join(f'"{column_name}"' for column_name in column_names)
    placeholders = ", ".join("?" for _ in column_names)
    insert_sql = (
        f'INSERT INTO "{table_name}" ({quoted_columns}) VALUES ({placeholders})'
    )
    select_sql = f'SELECT {quoted_columns} FROM "{schema_name}"."{table_name}"'

    with pg_conn.cursor() as cursor:
        cursor.execute(select_sql)
        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break
            sqlite_conn.executemany(
                insert_sql,
                [tuple(normalize_value(value) for value in row) for row in rows],
            )

    for statement in INDEX_STATEMENTS.get(table_name, []):
        sqlite_conn.execute(statement)


def build_dump(settings_module):
    source_database = getattr(settings_module, "SOURCE_DATABASE", None)
    if not source_database:
        raise SystemExit(
            "SOURCE_DATABASE is required in scripts/dump_legacy_to_sqlite_settings.py"
        )

    source_schema = getattr(settings_module, "SOURCE_SCHEMA", "public")
    batch_size = int(getattr(settings_module, "BATCH_SIZE", 2000))
    target_path = Path(getattr(settings_module, "TARGET_SQLITE_PATH"))
    if not target_path.is_absolute():
        target_path = REPO_ROOT / target_path
    target_path.parent.mkdir(parents=True, exist_ok=True)

    temp_path = target_path.with_suffix(f"{target_path.suffix}.tmp")
    if temp_path.exists():
        temp_path.unlink()

    with psycopg.connect(**source_database) as pg_conn:
        sqlite_conn = sqlite3.connect(temp_path)
        try:
            sqlite_conn.execute("PRAGMA journal_mode=WAL")
            sqlite_conn.execute("PRAGMA synchronous=NORMAL")
            sqlite_conn.execute("BEGIN")
            for table_name in LEGACY_TABLES:
                copy_table(pg_conn, sqlite_conn, source_schema, table_name, batch_size)
            sqlite_conn.commit()
        except Exception:
            sqlite_conn.rollback()
            raise
        finally:
            sqlite_conn.close()

    temp_path.replace(target_path)
    return target_path


def main():
    settings_module = load_local_settings()
    target_path = Path(getattr(settings_module, "TARGET_SQLITE_PATH"))
    if not target_path.is_absolute():
        target_path = REPO_ROOT / target_path
    lock_path = target_path.with_suffix(f"{target_path.suffix}.lock")
    lock_file = open_lock(lock_path)
    try:
        dumped_path = build_dump(settings_module)
    finally:
        lock_file.close()
    print(f"Legacy SQLite dump refreshed: {dumped_path}")


if __name__ == "__main__":
    main()
