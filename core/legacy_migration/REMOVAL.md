# Removing legacy_migration

Everything for the one-time legacy import lives in this package, except for
these hooks into the rest of the app. Delete them together:

1. `core/legacy_migration/` (this package).
2. `core/management/commands/import_legacy_data.py` and
   `import_legacy_media.py`.
3. The `LegacySourceMap` import at the bottom of `core/models.py`, then run
   `makemigrations` to drop the `core_legacysourcemap` table.
4. `core/tests/test_import_legacy_media.py` and
   `core/tests/test_legacy_annotations.py`.
5. `scripts/dump_legacy_to_sqlite.py` and
   `scripts/dump_legacy_to_sqlite_settings_template.py`.
6. `LEGACY_MIGRATION_MEDIA_ROOT` in `yvideo/settings.py` and
   `yvideo/secret_settings_template.py`.
7. `LEGACY_MIGRATION.md` and the "Legacy Migration" section of `README.md`.
8. `LEGACY_DATA_LANGUAGES` in `core/language_data.py` and
   `test_covers_languages_in_legacy_dump` in `core/tests/test_language_seed.py`,
   if no imported subtitles still use those languages.
9. The `scripts/dump_legacy_to_sqlite_settings.py` entries in `.gitignore`
   and `scripts/new_worktree.sh`.
10. Section 6 of `MANUAL_TESTING.md` and its row in the table at the top.
