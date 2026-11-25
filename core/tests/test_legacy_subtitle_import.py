from pathlib import Path
import sqlite3
import tempfile

from django.test import TransactionTestCase

from core.factories import LanguageFactory
from core.legacy_migration.bulk_import import REQUIRED_COLUMNS
from core.legacy_migration.bulk_import import LegacyBulkImporter
from core.models import Content


class LegacySubtitleImportTests(TransactionTestCase):
    def test_imported_subtitles_are_added_to_their_content(self):
        LanguageFactory(bcp47="en", language="English")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
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
            db.execute(
                "INSERT INTO resources (id, resource_name, resource_type, requester_email,"
                " copyrighted, physical_copy_exists, full_video, views, metadata)"
                " VALUES ('r1', 'Lecture', 'video', '', 'false', 'false', 'true', 0, '')"
            )
            db.execute(
                "INSERT INTO contents (id, title, collection_id, resource_id, file_id,"
                " file_version, url, description, allow_definitions, allow_captions, views,"
                " published, annotations, clips)"
                " VALUES ('k1', 'Lecture', 'c1', 'r1', '', '',"
                " 'https://www.youtube.com/watch?v=eHEsJyVQn3w', '', 'true', 'true', 0,"
                " 'true', NULL, NULL)"
            )
            db.execute(
                "INSERT INTO subtitles (id, title, language, content, content_id)"
                " VALUES ('s1', 'English', 'en', '[]', 'k1')"
            )

        LegacyBulkImporter(source=source, yearterm="20265", commit=True).run()

        content = Content.objects.get(title="Lecture")
        self.assertEqual(
            [track.name for track in content.subtitle_tracks.all()], ["English"]
        )
