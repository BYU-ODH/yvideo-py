#!/bin/bash

set -e

FAIL_MSG_INSTRUCTIONS="In order to upgrade this fixture, load the fixture before applying the latest migration, then apply the migration, then dump the data again. See scripts/dump_fixtures.sh for an example."
# Set test database name (adjust as needed)
PRECOMMIT_DB="default"  # Use 'default' since 'precommit_fixture_check' is not defined in settings

# Create test database (SQLite example; adjust for your DB)
rm -f "db.sqlite3"

# Run migrations on test DB
uv run manage.py migrate --database="${PRECOMMIT_DB}"

# Find all fixture files (adjust path/pattern as needed)
for fixture in $(find ./fixtures -name '*.json'); do
    FAIL_MSG="${fixture} is out of date. ${FAIL_MSG_INSTRUCTIONS}"
    echo "Checking fixture: ${fixture}"
    uv run manage.py loaddata "${fixture}" --database="${PRECOMMIT_DB}" || { echo "${FAIL_MSG}"; exit 1; }
done

# Clean up test database
rm -f "db.sqlite3"

echo "All fixtures are up-to-date with migrations."
