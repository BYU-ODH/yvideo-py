# Copy this file to secret_settings.py
# NEVER COMMIT secret_settings.py to the repository!
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver"]
DEBUG = True
DEV_QUICK_LOGIN_ENABLED = False
# Development-only fallback so local runs and CI tests work without secret_settings.py.
SECRET_KEY = "dev-only-insecure-secret-key"
TIME_ZONE = "America/Denver"
API_CLIENT_ID = ""
API_CLIENT_SECRET = ""
# For URLs that contain query string variables that differ based on logged in user,
# provide the entire url up to the '?', exclusive.
# example: api.example.com/v1/?some_variable=some_value should be recorded here as:
# API_EXAMPLE = "api.example.com/v1/"
# a method using this url will append the ?some_variable=some_value to the end of the url
API_AUTH_TOKEN_URL = ""
API_YEARTERM_URL = ""
API_WORKER_ID_IAM_URL = ""
API_WORKER_SUMMARY_URL = ""
API_STUDENT_SUMMARY_URL = ""
API_STUDENT_ENROLLMENTS_URL = ""
LEGACY_MIGRATION_ENABLED = False
LEGACY_MIGRATION_MEDIA_ROOT = ""
LEGACY_MIGRATION_DB_ALIAS = "legacy"
LEGACY_MIGRATION_SQLITE_PATH = "var/legacy_migration/legacy_dump.sqlite3"
LEGACY_MIGRATION_CREATE_MISSING_USERS = False
LEGACY_MIGRATION_AUTO_DUMP_ENABLED = True
LEGACY_MIGRATION_AUTO_DUMP_HOUR = 3
# The app only reads a local SQLite snapshot for legacy migration.
# Refresh the snapshot externally or with scripts/dump_legacy_to_sqlite.py.
