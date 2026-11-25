import sqlite3

from django.test import TransactionTestCase

from core.factories import LanguageFactory
from core.legacy_migration.bulk_import import LegacyBulkImporter
from core.models import Content
from core.testing import make_legacy_source


class LegacySubtitleImportTests(TransactionTestCase):
    def test_imported_subtitles_are_added_to_their_content(self):
        LanguageFactory(bcp47="en", language="English")
        source = make_legacy_source(self)
        with sqlite3.connect(source) as db:
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
