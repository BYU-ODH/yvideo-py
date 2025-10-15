#!/bin/bash

set -e

FAIL_MSG_INSTRUCTIONS="In order to upgrade this fixture, load the fixture before applying the latest migration, then apply the migration, then dump the data again. See scripts/dump_fixtures.sh for an example."

ORIGINAL_DB="db.sqlite3"
BACKUP_DB="db.sqlite3.bak"
TEST_DB="default"

# Backup the original database if it exists
if [ -f "${ORIGINAL_DB}" ]; then
    mv "${ORIGINAL_DB}" "${BACKUP_DB}"
fi

# Remove any leftover test database
rm -f "${TEST_DB}"

# Run migrations on test DB
uv run manage.py migrate --database="${TEST_DB}"

# Find all fixture files (adjust path/pattern as needed)
for fixture in $(find ./fixtures -name '*.json'); do
    FAIL_MSG="${fixture} is out of date. ${FAIL_MSG_INSTRUCTIONS}"
    echo "Checking fixture: ${fixture}"
    uv run manage.py loaddata "${fixture}" --database="${TEST_DB}" || { echo "${FAIL_MSG}"; [ -f "${BACKUP_DB}" ] && mv "${BACKUP_DB}" "${ORIGINAL_DB}"; exit 1; }
done

# Clean up test database
rm -f "${TEST_DB}"

# Restore the original database if it was backed up
if [ -f "${BACKUP_DB}" ]; then
    mv "${BACKUP_DB}" "${ORIGINAL_DB}"
fi

echo "All fixtures are up-to-date with migrations."
