import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from unittest import mock
import uuid

from django.conf import settings
from django.contrib import admin
from django.contrib import messages
from django.contrib.auth.models import Group
from django.contrib.messages import get_messages
from django.core.exceptions import ImproperlyConfigured
from django.db import OperationalError
from django.db import connection
from django.db import connections
from django.test import Client
from django.test import TestCase
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
import xxhash

from ..admin_legacy_migration import LegacyMigrationFileDecisionForm
from ..admin_legacy_migration import LegacyMigrationRequestAdmin
from ..admin_legacy_migration import LegacyMigrationResourceInline
from ..factories import ContentFactory
from ..factories import LanguageFactory
from ..factories import PlaylistFactory
from ..factories import ResourceFactory
from ..factories import UserFactory
from ..legacy_migration import ChecksumCache
from ..legacy_migration import LegacyCatalogClient
from ..legacy_migration import LegacyMigrationFileAction
from ..legacy_migration import LegacyMigrationFileDecision
from ..legacy_migration import LegacyMigrationIssueSeverity
from ..legacy_migration import LegacyMigrationJob
from ..legacy_migration import LegacyMigrationJobCanceled
from ..legacy_migration import LegacyMigrationJobStatus
from ..legacy_migration import LegacyMigrationJobType
from ..legacy_migration import LegacyMigrationRequest
from ..legacy_migration import LegacyMigrationResource
from ..legacy_migration import LegacyMigrationService
from ..legacy_migration import LegacyMigrationStatus
from ..legacy_migration import LegacyMigrationUserResolutionStatus
from ..legacy_migration import LegacySourceMap
from ..legacy_migration import dump as legacy_dump
from ..legacy_migration.parsers import LegacyFileInfo
from ..models import LAB_ASSISTANT_GROUP_NAME
from ..models import BlankAnnotation
from ..models import BlurAnnotation
from ..models import BlurAnnotationPosition
from ..models import Clip
from ..models import Content
from ..models import MuteAnnotation
from ..models import PauseAnnotation
from ..models import Playlist
from ..models import PlaylistRole
from ..models import PlaylistUserAccess
from ..models import Resource
from ..models import ResourceAccess
from ..models import ResourceFile
from ..models import SkipAnnotation
from ..models import Subtitle


@override_settings(
    DEBUG=True,
    LEGACY_MIGRATION_ENABLED=True,
    LEGACY_MIGRATION_MEDIA_ROOT="",
)
class LegacyMigrationTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._media_root = tempfile.mkdtemp(prefix="legacy-migration-media-")
        cls._settings = override_settings(
            MEDIA_ROOT=cls._media_root,
            LEGACY_MIGRATION_MEDIA_ROOT=cls._media_root,
            ALLOWED_HOSTS=["localhost", "127.0.0.1", "testserver"],
            SECRET_KEY="test-secret-key",
        )
        cls._settings.enable()

    @classmethod
    def tearDownClass(cls):
        cls._settings.disable()
        shutil.rmtree(cls._media_root, ignore_errors=True)
        super().tearDownClass()

    def tearDown(self):
        table_names = [
            "content_subtitles_assoc",
            "subtitles",
            "contents",
            "file_keys",
            "files",
            "resource_access",
            "resources",
            "collection_courses_assoc",
            "courses",
            "user_collections_assoc",
            "collections",
            "users",
        ]
        with connection.cursor() as cursor:
            for table_name in table_names:
                cursor.execute(f"DROP TABLE IF EXISTS {table_name}")
        super().tearDown()

    def create_legacy_schema(self):
        statements = [
            """
            CREATE TABLE users (
                id TEXT PRIMARY KEY,
                deleted TIMESTAMP NULL,
                username TEXT,
                byu_person_id TEXT,
                email TEXT
            )
            """,
            """
            CREATE TABLE collections (
                id TEXT PRIMARY KEY,
                deleted TIMESTAMP NULL,
                collection_name TEXT,
                owner TEXT,
                published BOOLEAN,
                archived BOOLEAN,
                public BOOLEAN,
                copyrighted BOOLEAN
            )
            """,
            """
            CREATE TABLE user_collections_assoc (
                id TEXT PRIMARY KEY,
                deleted TIMESTAMP NULL,
                username TEXT,
                collection_id TEXT,
                account_role INTEGER
            )
            """,
            """
            CREATE TABLE courses (
                id TEXT PRIMARY KEY,
                deleted TIMESTAMP NULL,
                department TEXT,
                catalog_number TEXT,
                section_number TEXT
            )
            """,
            """
            CREATE TABLE collection_courses_assoc (
                id TEXT PRIMARY KEY,
                deleted TIMESTAMP NULL,
                collection_id TEXT,
                course_id TEXT
            )
            """,
            """
            CREATE TABLE resources (
                id TEXT PRIMARY KEY,
                deleted TIMESTAMP NULL,
                resource_name TEXT,
                resource_type TEXT,
                requester_email TEXT,
                copyrighted BOOLEAN,
                physical_copy_exists BOOLEAN,
                full_video BOOLEAN,
                published BOOLEAN,
                views INTEGER,
                metadata TEXT
            )
            """,
            """
            CREATE TABLE resource_access (
                id TEXT PRIMARY KEY,
                deleted TIMESTAMP NULL,
                username TEXT,
                resource_id TEXT
            )
            """,
            """
            CREATE TABLE files (
                id TEXT PRIMARY KEY,
                deleted TIMESTAMP NULL,
                resource_id TEXT,
                filepath TEXT,
                file_version TEXT,
                metadata TEXT,
                created TIMESTAMP NULL,
                updated TIMESTAMP NULL
            )
            """,
            """
            CREATE TABLE file_keys (
                id TEXT PRIMARY KEY,
                deleted TIMESTAMP NULL,
                updated TIMESTAMP NULL,
                created TIMESTAMP NULL,
                file_id TEXT,
                user_id TEXT
            )
            """,
            """
            CREATE TABLE contents (
                id TEXT PRIMARY KEY,
                deleted TIMESTAMP NULL,
                created TIMESTAMP NULL,
                collection_id TEXT,
                resource_id TEXT,
                title TEXT,
                content_type TEXT,
                url TEXT,
                description TEXT,
                tags TEXT,
                annotations TEXT,
                thumbnail TEXT,
                allow_definitions BOOLEAN,
                allow_notes BOOLEAN,
                allow_captions BOOLEAN,
                views INTEGER,
                file_version TEXT,
                published BOOLEAN,
                clips TEXT
            )
            """,
            """
            CREATE TABLE subtitles (
                id TEXT PRIMARY KEY,
                deleted TIMESTAMP NULL,
                created TIMESTAMP NULL,
                title TEXT,
                language TEXT,
                content TEXT,
                content_id TEXT
            )
            """,
            """
            CREATE TABLE content_subtitles_assoc (
                id TEXT PRIMARY KEY,
                deleted TIMESTAMP NULL,
                content_id TEXT,
                subtitle_id TEXT
            )
            """,
        ]
        with connection.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)

    def insert_legacy_row(self, table_name, payload):
        columns = ", ".join(payload.keys())
        placeholders = ", ".join(["%s"] * len(payload))
        with connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {table_name} ({columns}) VALUES ({placeholders})",
                list(payload.values()),
            )

    def write_media_file(self, relative_path, payload=b"legacy-video"):
        path = Path(settings.MEDIA_ROOT) / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path

    def build_service(self):
        return LegacyMigrationService(
            catalog_client=LegacyCatalogClient(alias="default")
        )

    def test_legacy_catalog_client_rejects_non_sqlite_engine(self):
        with mock.patch.dict(
            connections.databases,
            {
                "legacy_postgres": {
                    "ENGINE": "django.db.backends.postgresql",
                    "NAME": "legacy",
                }
            },
            clear=False,
        ):
            with self.assertRaisesMessage(
                ImproperlyConfigured,
                "Legacy migration only supports SQLite dumps.",
            ):
                LegacyCatalogClient(alias="legacy_postgres")

    def test_preflight_builds_file_candidates_and_fuzzy_name_issue(self):
        self.create_legacy_schema()
        language = LanguageFactory(language="English", bcp47="en")

        owner = UserFactory(netid="profada", username="123456789", instructor=True)
        ta_user = UserFactory(netid="caseyta", username="987654321", instructor=True)
        current_resource = ResourceFactory(
            name="Legacy Birds", requester_username=owner.username
        )
        shared_path = self.write_media_file("legacy/shared-birds.mp4")
        current_resource_file = ResourceFile(
            resource=current_resource,
            version="english",
            full_video=True,
        )
        current_resource_file.file.name = "legacy/shared-birds.mp4"
        current_resource_file.save()
        current_playlist = PlaylistFactory(owner=owner, name="Current Birds Playlist")
        PlaylistUserAccess.objects.create(
            user=ta_user,
            playlist=current_playlist,
            playlist_role=PlaylistRole.TA,
        )
        Content.objects.create(
            playlist=current_playlist,
            title="Current Birds Content",
            resource=current_resource,
            resource_file=current_resource_file,
            published=True,
        )
        Subtitle.objects.create(
            resource=current_resource,
            owner=owner,
            language=language,
            name="Current English",
            subtitles_file="legacy/current.vtt",
            is_original=True,
        )

        legacy_collection_id = str(uuid.uuid4())
        legacy_resource_id = str(uuid.uuid4())
        legacy_owner_id = str(uuid.uuid4())
        legacy_ta_id = str(uuid.uuid4())
        legacy_content_id = str(uuid.uuid4())
        legacy_url_content_id = str(uuid.uuid4())

        self.insert_legacy_row(
            "users",
            {
                "id": legacy_owner_id,
                "deleted": None,
                "username": "profada",
                "byu_person_id": "123456789",
                "email": "profada@example.test",
            },
        )
        self.insert_legacy_row(
            "users",
            {
                "id": legacy_ta_id,
                "deleted": None,
                "username": "caseyta",
                "byu_person_id": "987654321",
                "email": "caseyta@example.test",
            },
        )
        self.insert_legacy_row(
            "collections",
            {
                "id": legacy_collection_id,
                "deleted": None,
                "collection_name": "Legacy Birds Shelf",
                "owner": legacy_owner_id,
                "published": 1,
                "archived": 0,
                "public": 0,
                "copyrighted": 1,
            },
        )
        self.insert_legacy_row(
            "user_collections_assoc",
            {
                "id": str(uuid.uuid4()),
                "deleted": None,
                "username": "caseyta",
                "collection_id": legacy_collection_id,
                "account_role": 1,
            },
        )
        self.insert_legacy_row(
            "resources",
            {
                "id": legacy_resource_id,
                "deleted": None,
                "resource_name": "Legacy Birds",
                "resource_type": "video",
                "requester_email": "profada@example.test",
                "copyrighted": 1,
                "physical_copy_exists": 0,
                "full_video": 1,
                "published": 1,
                "views": 11,
                "metadata": "{}",
            },
        )
        self.insert_legacy_row(
            "resource_access",
            {
                "id": str(uuid.uuid4()),
                "deleted": None,
                "username": "caseyta",
                "resource_id": legacy_resource_id,
            },
        )
        self.insert_legacy_row(
            "files",
            {
                "id": str(uuid.uuid4()),
                "deleted": None,
                "resource_id": legacy_resource_id,
                "filepath": "legacy/shared-birds.mp4",
                "file_version": "english",
                "metadata": "{}",
                "created": "2026-01-01 00:00:00",
                "updated": "2026-01-01 00:00:00",
            },
        )
        self.insert_legacy_row(
            "contents",
            {
                "id": legacy_content_id,
                "deleted": None,
                "created": "2026-01-01 00:00:00",
                "collection_id": legacy_collection_id,
                "resource_id": legacy_resource_id,
                "title": "Bird Lesson",
                "content_type": "video",
                "url": "",
                "description": "A bird lesson",
                "tags": "",
                "annotations": json.dumps([]),
                "thumbnail": "",
                "allow_definitions": 1,
                "allow_notes": 1,
                "allow_captions": 1,
                "views": 5,
                "file_version": "english",
                "published": 1,
                "clips": json.dumps([]),
            },
        )
        self.insert_legacy_row(
            "contents",
            {
                "id": legacy_url_content_id,
                "deleted": None,
                "created": "2026-01-01 00:00:00",
                "collection_id": legacy_collection_id,
                "resource_id": "00000000-0000-0000-0000-000000000000",
                "title": "Bird URL Lesson",
                "content_type": "web",
                "url": "https://example.com/birds.mp4",
                "description": "URL only",
                "tags": "",
                "annotations": json.dumps([]),
                "thumbnail": "",
                "allow_definitions": 1,
                "allow_notes": 1,
                "allow_captions": 1,
                "views": 2,
                "file_version": "",
                "published": 1,
                "clips": json.dumps([]),
            },
        )
        self.insert_legacy_row(
            "subtitles",
            {
                "id": str(uuid.uuid4()),
                "deleted": None,
                "created": "2026-01-01 00:00:00",
                "title": "English",
                "language": "English",
                "content": json.dumps([{"start": 0, "end": 1, "text": "Birds"}]),
                "content_id": legacy_content_id,
            },
        )

        migration_request = LegacyMigrationRequest.objects.create(
            requested_by=owner,
            target_owner=owner,
            migration_kind="collection",
            legacy_reference=legacy_collection_id,
        )

        service = self.build_service()
        service.preflight_request(migration_request)
        migration_request.refresh_from_db()

        self.assertEqual(migration_request.status, LegacyMigrationStatus.NEEDS_REVIEW)
        self.assertEqual(migration_request.migration_resources.count(), 2)
        file_decision = migration_request.file_decisions.get()
        self.assertEqual(file_decision.size_bytes, shared_path.stat().st_size)
        self.assertEqual(file_decision.metadata["absolute_path"], str(shared_path))
        self.assertTrue(file_decision.metadata["mtime_ns"])
        self.assertEqual(
            file_decision.candidate_matches[0]["match_reason"], "same_realpath"
        )
        self.assertNotIn("collections", file_decision.candidate_matches[0])
        self.assertNotIn("instructors", file_decision.candidate_matches[0])
        self.assertEqual(file_decision.linked_contents, [])
        self.assertEqual(file_decision.linked_collections, [])
        self.assertEqual(file_decision.linked_instructors, [])
        self.assertTrue(
            migration_request.issues.filter(
                code="duplicate_file_requires_decision",
                severity="blocking",
            ).exists()
        )
        self.assertTrue(
            migration_request.issues.filter(
                code="similar_resource_name", severity="blocking"
            ).exists()
        )
        self.assertFalse(
            migration_request.issues.filter(code="unresolved_user").exists()
        )

        real_resource = migration_request.migration_resources.filter(
            is_synthetic=False
        ).get()
        real_resource.include = False
        real_resource.save(update_fields=["include", "updated_at"])
        service.sync_request_issues(migration_request)
        self.assertEqual(migration_request.issues.count(), 0)

    def test_build_fuzzy_matches_ranks_by_name_similarity(self):
        # Scores below come from the difflib.SequenceMatcher ratio that backs
        # _build_fuzzy_matches; they're deterministic for these fixed strings.
        exact_variant = ResourceFactory(name="INTRODUCTION TO BYZANTINE ART!!")
        near_duplicate = ResourceFactory(name="Introduction to Byzantin Art")
        moderate_variant = ResourceFactory(
            name="Introduction to Byzantine Art (Revised)"
        )
        weak_variant = ResourceFactory(name="Byzantine Art")
        unrelated = ResourceFactory(name="Quantum Computing Basics")
        # Lowest-scoring candidate: exercises the top-5 cap below.
        ResourceFactory(name="Cooking with Cast Iron")

        service = LegacyMigrationService(require_catalog=False)
        matches = service._build_fuzzy_matches("Introduction to Byzantine Art")

        # Only the 5 highest-scoring candidates are kept.
        self.assertEqual(len(matches), 5)
        self.assertNotIn(
            "cooking with cast iron", [m["normalized_name"] for m in matches]
        )

        scores_by_resource_id = {m["resource_id"]: m["score"] for m in matches}
        # Punctuation/case-only differences normalize away entirely.
        self.assertEqual(scores_by_resource_id[exact_variant.pk], 100)
        # A one-letter typo still lands solidly in "likely the same resource" territory.
        self.assertEqual(scores_by_resource_id[near_duplicate.pk], 98)
        # A trailing qualifier is a lower but still strong match.
        self.assertEqual(scores_by_resource_id[moderate_variant.pk], 87)
        # Sharing only some of the words scores much lower.
        self.assertEqual(scores_by_resource_id[weak_variant.pk], 61)
        # An unrelated name scores lowest among the kept candidates.
        self.assertEqual(scores_by_resource_id[unrelated.pk], 30)

        # Results are sorted best-match-first.
        self.assertEqual(
            [m["resource_id"] for m in matches],
            [
                exact_variant.pk,
                near_duplicate.pk,
                moderate_variant.pk,
                weak_variant.pk,
                unrelated.pk,
            ],
        )

    def test_resolve_language_rejects_cakchiquel_legacy_default(self):
        # The legacy system used Cakchiquel as a bogus default subtitle
        # language, so migration must never auto-resolve it, even though a
        # matching Language row exists (an admin may still pick it manually).
        LanguageFactory(language="Cakchiquel", bcp47="cak")
        service = LegacyMigrationService(require_catalog=False)

        self.assertIsNone(service._resolve_language("Cakchiquel"))
        self.assertIsNone(service._resolve_language("cak"))
        self.assertIsNone(service._resolve_language("CAKCHIQUEL"))

    def test_resolve_language_still_resolves_trustworthy_languages(self):
        english = LanguageFactory(language="English", bcp47="en")

        service = LegacyMigrationService(require_catalog=False)

        self.assertEqual(service._resolve_language("English"), english)
        self.assertEqual(service._resolve_language("en"), english)
        self.assertEqual(service._resolve_language("EN"), english)

    def test_preflight_flags_cakchiquel_subtitle_language_for_manual_review(self):
        # A subtitle's raw language must be attached to real, file-backed
        # content: the preflight subtitle-language check is skipped entirely
        # for synthetic (URL-only) content.
        self.create_legacy_schema()
        LanguageFactory(language="Cakchiquel", bcp47="cak")

        owner = UserFactory(netid="profcak", username="333333333", instructor=True)
        legacy_collection_id = str(uuid.uuid4())
        legacy_owner_id = str(uuid.uuid4())
        legacy_resource_id = str(uuid.uuid4())
        legacy_content_id = str(uuid.uuid4())
        legacy_file_id = str(uuid.uuid4())
        self.write_media_file("legacy/cak-video.mp4", payload=b"video-payload")

        self.insert_legacy_row(
            "users",
            {
                "id": legacy_owner_id,
                "deleted": None,
                "username": "profcak",
                "byu_person_id": "333333333",
                "email": "profcak@example.test",
            },
        )
        self.insert_legacy_row(
            "collections",
            {
                "id": legacy_collection_id,
                "deleted": None,
                "collection_name": "Legacy Cakchiquel Collection",
                "owner": legacy_owner_id,
                "published": 1,
                "archived": 0,
                "public": 0,
                "copyrighted": 0,
            },
        )
        self.insert_legacy_row(
            "resources",
            {
                "id": legacy_resource_id,
                "deleted": None,
                "resource_name": "Cakchiquel Video Resource",
                "resource_type": "video",
                "requester_email": "profcak@example.test",
                "copyrighted": 0,
                "physical_copy_exists": 1,
                "full_video": 1,
                "published": 1,
                "views": 0,
                "metadata": "{}",
            },
        )
        self.insert_legacy_row(
            "files",
            {
                "id": legacy_file_id,
                "deleted": None,
                "resource_id": legacy_resource_id,
                "filepath": "legacy/cak-video.mp4",
                "file_version": "original",
                "metadata": "{}",
                "created": "2026-01-01 00:00:00",
                "updated": "2026-01-01 00:00:00",
            },
        )
        self.insert_legacy_row(
            "contents",
            {
                "id": legacy_content_id,
                "deleted": None,
                "created": "2026-01-01 00:00:00",
                "collection_id": legacy_collection_id,
                "resource_id": legacy_resource_id,
                "title": "Cakchiquel Video Lesson",
                "content_type": "video",
                "url": "",
                "description": "",
                "tags": "",
                "annotations": json.dumps([]),
                "thumbnail": "",
                "allow_definitions": 1,
                "allow_notes": 1,
                "allow_captions": 1,
                "views": 0,
                "file_version": "original",
                "published": 1,
                "clips": json.dumps([]),
            },
        )
        self.insert_legacy_row(
            "subtitles",
            {
                "id": str(uuid.uuid4()),
                "deleted": None,
                "created": "2026-01-01 00:00:00",
                "title": "Cakchiquel",
                "language": "Cakchiquel",
                "content": json.dumps([{"start": 0, "end": 1, "text": "Hola"}]),
                "content_id": legacy_content_id,
            },
        )

        migration_request = LegacyMigrationRequest.objects.create(
            requested_by=owner,
            target_owner=owner,
            migration_kind="collection",
            legacy_reference=legacy_collection_id,
        )
        service = self.build_service()
        service.preflight_request(migration_request)
        migration_request.refresh_from_db()

        self.assertTrue(
            migration_request.issues.filter(
                code="missing_subtitle_language", severity="blocking"
            ).exists()
        )
        self.assertTrue(migration_request.has_blocking_issues())

    def test_import_collection_migrates_files_url_content_permissions_and_annotations(
        self,
    ):
        self.create_legacy_schema()
        LanguageFactory(language="English", bcp47="en")

        target_owner = UserFactory(
            netid="profben", username="111111111", instructor=True
        )
        playlist_ta = UserFactory(
            netid="caseyta", username="222222222", instructor=True
        )
        resource_guest = UserFactory(
            netid="resourceg",
            username="333333333",
            instructor=True,
        )

        legacy_collection_id = str(uuid.uuid4())
        legacy_resource_id = str(uuid.uuid4())
        legacy_owner_id = str(uuid.uuid4())
        legacy_ta_id = str(uuid.uuid4())
        legacy_resource_guest_id = str(uuid.uuid4())
        legacy_content_id = str(uuid.uuid4())
        legacy_url_content_id = str(uuid.uuid4())
        legacy_file_id = str(uuid.uuid4())
        legacy_course_id = str(uuid.uuid4())
        source_path = self.write_media_file(
            "legacy/import-video.mp4", payload=b"video-payload"
        )

        self.insert_legacy_row(
            "users",
            {
                "id": legacy_owner_id,
                "deleted": None,
                "username": "profben",
                "byu_person_id": "111111111",
                "email": "profben@example.test",
            },
        )
        self.insert_legacy_row(
            "users",
            {
                "id": legacy_ta_id,
                "deleted": None,
                "username": "caseyta",
                "byu_person_id": "222222222",
                "email": "caseyta@example.test",
            },
        )
        self.insert_legacy_row(
            "users",
            {
                "id": legacy_resource_guest_id,
                "deleted": None,
                "username": "resourceg",
                "byu_person_id": "333333333",
                "email": "resourceg@example.test",
            },
        )
        self.insert_legacy_row(
            "collections",
            {
                "id": legacy_collection_id,
                "deleted": None,
                "collection_name": "Imported Legacy Shelf",
                "owner": legacy_owner_id,
                "published": 1,
                "archived": 0,
                "public": 1,
                "copyrighted": 1,
            },
        )
        self.insert_legacy_row(
            "user_collections_assoc",
            {
                "id": str(uuid.uuid4()),
                "deleted": None,
                "username": "caseyta",
                "collection_id": legacy_collection_id,
                "account_role": 1,
            },
        )
        self.insert_legacy_row(
            "courses",
            {
                "id": legacy_course_id,
                "deleted": None,
                "department": "FILM",
                "catalog_number": "101",
                "section_number": "001",
            },
        )
        self.insert_legacy_row(
            "collection_courses_assoc",
            {
                "id": str(uuid.uuid4()),
                "deleted": None,
                "collection_id": legacy_collection_id,
                "course_id": legacy_course_id,
            },
        )
        self.insert_legacy_row(
            "resources",
            {
                "id": legacy_resource_id,
                "deleted": None,
                "resource_name": "Imported Video Resource",
                "resource_type": "video",
                "requester_email": "profben@example.test",
                "copyrighted": 1,
                "physical_copy_exists": 1,
                "full_video": 1,
                "published": 1,
                "views": 21,
                "metadata": '{"source":"legacy"}',
            },
        )
        self.insert_legacy_row(
            "resource_access",
            {
                "id": str(uuid.uuid4()),
                "deleted": None,
                "username": "resourceg",
                "resource_id": legacy_resource_id,
            },
        )
        self.insert_legacy_row(
            "files",
            {
                "id": legacy_file_id,
                "deleted": None,
                "resource_id": legacy_resource_id,
                "filepath": "legacy/import-video.mp4",
                "file_version": "english",
                "metadata": '{"title":"Imported Video"}',
                "created": "2026-01-01 00:00:00",
                "updated": "2026-01-01 00:00:00",
            },
        )
        self.insert_legacy_row(
            "contents",
            {
                "id": legacy_content_id,
                "deleted": None,
                "created": "2026-01-01 00:00:00",
                "collection_id": legacy_collection_id,
                "resource_id": legacy_resource_id,
                "title": "Imported Lecture",
                "content_type": "video",
                "url": "",
                "description": "Imported description",
                "tags": "birds; migration",
                "annotations": json.dumps(
                    [
                        {
                            "type": "Comment",
                            "layer": 0,
                            "start": 1,
                            "end": 3,
                            "comment": "Discuss this scene",
                            "position": {"x": 10, "y": 20},
                        },
                        {
                            "type": "Censor",
                            "layer": 1,
                            "start": 2,
                            "end": 6,
                            # [time, centerX, centerY, width, height] - legacy geometry is
                            # center-anchored. Values chosen so the converted top-left corner
                            # stays on the frame, since a clamp to 0 would hide whether the
                            # conversion happened at all.
                            "position": {
                                "0": [2, 40, 50, 20, 30],
                                "1": [4, 65, 60, 10, 20],
                            },
                        },
                    ]
                ),
                "thumbnail": "",
                "allow_definitions": 1,
                "allow_notes": 1,
                "allow_captions": 1,
                "views": 9,
                "file_version": "english",
                "published": 1,
                "clips": json.dumps([{"title": "Intro", "start": 1, "end": 3}]),
            },
        )
        self.insert_legacy_row(
            "contents",
            {
                "id": legacy_url_content_id,
                "deleted": None,
                "created": "2026-01-01 00:00:00",
                "collection_id": legacy_collection_id,
                "resource_id": "00000000-0000-0000-0000-000000000000",
                "title": "Imported URL Content",
                "content_type": "web",
                "url": "https://example.com/imported.mp4",
                "description": "External media",
                "tags": "",
                "annotations": json.dumps([]),
                "thumbnail": "",
                "allow_definitions": 1,
                "allow_notes": 1,
                "allow_captions": 1,
                "views": 1,
                "file_version": "",
                "published": 0,
                "clips": json.dumps([]),
            },
        )
        self.insert_legacy_row(
            "subtitles",
            {
                "id": str(uuid.uuid4()),
                "deleted": None,
                "created": "2026-01-01 00:00:00",
                "title": "English",
                "language": "English",
                "content": json.dumps(
                    [{"start": 0.0, "end": 2.0, "text": "Hello world"}]
                ),
                "content_id": legacy_content_id,
            },
        )

        migration_request = LegacyMigrationRequest.objects.create(
            requested_by=target_owner,
            target_owner=target_owner,
            migration_kind="collection",
            legacy_reference=legacy_collection_id,
        )
        service = self.build_service()
        service.preflight_request(migration_request)
        migration_request.refresh_from_db()
        self.assertEqual(migration_request.issues.count(), 0)

        job = service.approve_and_queue_import(migration_request)
        service.run_job(job)
        migration_request.refresh_from_db()

        imported_playlist = Playlist.objects.get(name="Imported Legacy Shelf")
        imported_contents = Content.objects.filter(playlist=imported_playlist).order_by(
            "title"
        )
        self.assertEqual(imported_contents.count(), 2)
        imported_video = imported_contents.get(title="Imported Lecture")
        imported_url = imported_contents.get(title="Imported URL Content")
        self.assertIsNotNone(imported_video.resource_file)
        self.assertEqual(
            imported_video.resource_id, imported_video.resource_file.resource_id
        )
        self.assertIsNone(imported_url.resource_file)
        self.assertEqual(imported_url.resource.media_type, Resource.MediaType.WEB)
        self.assertEqual(imported_url.url, "https://example.com/imported.mp4")
        self.assertTrue(imported_playlist.courses.filter(dept="FILM").exists())

        imported_file_path = Path(imported_video.resource_file.file.path)
        self.assertEqual(
            os.stat(source_path).st_ino, os.stat(imported_file_path).st_ino
        )

        self.assertEqual(
            Clip.objects.filter(
                track__annotation_set=imported_video.annotation_set
            ).count(),
            1,
        )
        self.assertEqual(imported_video.annotation_set.tracks.count(), 3)
        self.assertEqual(
            BlurAnnotation.objects.filter(
                track__annotation_set=imported_video.annotation_set
            ).count(),
            1,
        )
        # Center-anchored legacy geometry converted to this app's top-left corner, through the
        # whole job rather than through _import_annotations alone: center (40, 50) of a 20x30
        # box is top-left (30, 35). See core/tests/test_legacy_blur_import.py for the cases.
        self.assertEqual(
            [
                (position.time, position.x, position.y, position.width, position.height)
                for position in BlurAnnotationPosition.objects.filter(
                    blur_annotation__track__annotation_set=imported_video.annotation_set
                )
            ],
            [(2.0, 30.0, 35.0, 20.0, 30.0), (4.0, 60.0, 50.0, 10.0, 20.0)],
        )

        subtitle = Subtitle.objects.get(resource=imported_video.resource)
        with subtitle.subtitles_file.open("r") as handle:
            self.assertIn("WEBVTT", handle.read())

        self.assertTrue(
            ResourceAccess.objects.filter(
                user=target_owner, resource=imported_video.resource
            ).exists()
        )
        self.assertTrue(
            ResourceAccess.objects.filter(
                user=resource_guest, resource=imported_video.resource
            ).exists()
        )
        self.assertFalse(
            ResourceAccess.objects.filter(
                user=playlist_ta, resource=imported_video.resource
            ).exists()
        )
        self.assertTrue(
            PlaylistUserAccess.objects.filter(
                user=target_owner,
                playlist=imported_playlist,
                playlist_role=PlaylistRole.INSTRUCTOR,
            ).exists()
        )
        self.assertTrue(
            PlaylistUserAccess.objects.filter(
                user=playlist_ta,
                playlist=imported_playlist,
                playlist_role=PlaylistRole.TA,
            ).exists()
        )
        self.assertFalse(
            PlaylistUserAccess.objects.filter(
                user=resource_guest,
                playlist=imported_playlist,
            ).exists()
        )
        self.assertEqual(migration_request.status, LegacyMigrationStatus.COMPLETED)
        self.assertTrue(
            LegacyMigrationJob.objects.filter(
                request=migration_request,
                job_type="import",
                status="completed",
            ).exists()
        )

    def test_preflight_auto_reuses_same_checksum_match(self):
        self.create_legacy_schema()

        owner = UserFactory(netid="profada", username="123456789", instructor=True)
        current_resource = ResourceFactory(
            name="Current Checksum Resource",
            requester_username=owner.username,
        )
        current_file_path = self.write_media_file(
            "current/existing-audio.mp3",
            payload=b"checksum-match-payload",
        )
        current_resource_file = ResourceFile(
            resource=current_resource,
            version="english",
            full_video=True,
        )
        current_resource_file.file.name = "current/existing-audio.mp3"
        current_resource_file.save()

        legacy_collection_id = str(uuid.uuid4())
        legacy_resource_id = str(uuid.uuid4())
        legacy_owner_id = str(uuid.uuid4())
        legacy_file_id = str(uuid.uuid4())
        legacy_content_id = str(uuid.uuid4())
        self.write_media_file(
            "legacy/incoming-audio.mp3",
            payload=b"checksum-match-payload",
        )

        self.insert_legacy_row(
            "users",
            {
                "id": legacy_owner_id,
                "deleted": None,
                "username": "profada",
                "byu_person_id": "123456789",
                "email": "profada@example.test",
            },
        )
        self.insert_legacy_row(
            "collections",
            {
                "id": legacy_collection_id,
                "deleted": None,
                "collection_name": "Checksum Legacy Shelf",
                "owner": legacy_owner_id,
                "published": 1,
                "archived": 0,
                "public": 0,
                "copyrighted": 1,
            },
        )
        self.insert_legacy_row(
            "resources",
            {
                "id": legacy_resource_id,
                "deleted": None,
                "resource_name": "Checksum Legacy Resource",
                "resource_type": "audio",
                "requester_email": "profada@example.test",
                "copyrighted": 1,
                "physical_copy_exists": 0,
                "full_video": 1,
                "published": 1,
                "views": 11,
                "metadata": "{}",
            },
        )
        self.insert_legacy_row(
            "files",
            {
                "id": legacy_file_id,
                "deleted": None,
                "resource_id": legacy_resource_id,
                "filepath": "legacy/incoming-audio.mp3",
                "file_version": "english",
                "metadata": "{}",
                "created": "2026-01-01 00:00:00",
                "updated": "2026-01-01 00:00:00",
            },
        )
        self.insert_legacy_row(
            "contents",
            {
                "id": legacy_content_id,
                "deleted": None,
                "created": "2026-01-01 00:00:00",
                "collection_id": legacy_collection_id,
                "resource_id": legacy_resource_id,
                "title": "Checksum Audio Lesson",
                "content_type": "audio",
                "url": "",
                "description": "Same bytes, new path",
                "tags": "",
                "annotations": json.dumps([]),
                "thumbnail": "",
                "allow_definitions": 1,
                "allow_notes": 1,
                "allow_captions": 1,
                "views": 5,
                "file_version": "english",
                "published": 1,
                "clips": json.dumps([]),
            },
        )

        migration_request = LegacyMigrationRequest.objects.create(
            requested_by=owner,
            target_owner=owner,
            migration_kind="collection",
            legacy_reference=legacy_collection_id,
        )

        service = self.build_service()
        service.preflight_request(migration_request)
        migration_request.refresh_from_db()

        file_decision = migration_request.file_decisions.get()
        migration_resource = migration_request.migration_resources.get(
            is_synthetic=False
        )
        self.assertEqual(
            file_decision.candidate_matches[0]["match_reason"],
            "same_checksum",
        )
        self.assertEqual(
            file_decision.action,
            LegacyMigrationFileAction.REUSE_EXISTING,
        )
        self.assertEqual(
            file_decision.selected_existing_resource_file_id,
            current_resource_file.pk,
        )
        self.assertEqual(
            migration_resource.selected_existing_resource_id,
            current_resource.pk,
        )
        self.assertFalse(
            migration_request.issues.filter(
                code="duplicate_file_requires_decision",
            ).exists()
        )
        self.assertEqual(
            file_decision.metadata["absolute_path"],
            str(Path(settings.MEDIA_ROOT) / "legacy/incoming-audio.mp3"),
        )
        self.assertNotEqual(
            file_decision.metadata["absolute_path"], str(current_file_path)
        )

    def test_preflight_does_not_auto_reuse_files_across_multiple_existing_resources(
        self,
    ):
        self.create_legacy_schema()

        owner = UserFactory(netid="profada", username="123456789", instructor=True)
        current_video_resource = ResourceFactory(
            name="Existing Video Resource",
            requester_username=owner.username,
        )
        current_video_file = ResourceFile(
            resource=current_video_resource,
            version="video",
            full_video=True,
        )
        self.write_media_file("current/existing-video.mp4", payload=b"video-bytes")
        current_video_file.file.name = "current/existing-video.mp4"
        current_video_file.save()

        current_audio_resource = ResourceFactory(
            name="Existing Audio Resource",
            requester_username=owner.username,
        )
        current_audio_file = ResourceFile(
            resource=current_audio_resource,
            version="audio",
            full_video=False,
        )
        self.write_media_file("current/existing-audio.mp3", payload=b"audio-bytes")
        current_audio_file.file.name = "current/existing-audio.mp3"
        current_audio_file.save()

        legacy_collection_id = str(uuid.uuid4())
        legacy_resource_id = str(uuid.uuid4())
        legacy_owner_id = str(uuid.uuid4())
        legacy_video_file_id = str(uuid.uuid4())
        legacy_audio_file_id = str(uuid.uuid4())
        legacy_content_id = str(uuid.uuid4())
        self.write_media_file("legacy/new-video.mp4", payload=b"video-bytes")
        self.write_media_file("legacy/new-audio.mp3", payload=b"audio-bytes")

        self.insert_legacy_row(
            "users",
            {
                "id": legacy_owner_id,
                "deleted": None,
                "username": "profada",
                "byu_person_id": "123456789",
                "email": "profada@example.test",
            },
        )
        self.insert_legacy_row(
            "collections",
            {
                "id": legacy_collection_id,
                "deleted": None,
                "collection_name": "Mixed Reuse Shelf",
                "owner": legacy_owner_id,
                "published": 1,
                "archived": 0,
                "public": 0,
                "copyrighted": 1,
            },
        )
        self.insert_legacy_row(
            "resources",
            {
                "id": legacy_resource_id,
                "deleted": None,
                "resource_name": "Mixed Reuse Resource",
                "resource_type": "video",
                "requester_email": "profada@example.test",
                "copyrighted": 1,
                "physical_copy_exists": 0,
                "full_video": 1,
                "published": 1,
                "views": 11,
                "metadata": "{}",
            },
        )
        self.insert_legacy_row(
            "files",
            {
                "id": legacy_video_file_id,
                "deleted": None,
                "resource_id": legacy_resource_id,
                "filepath": "legacy/new-video.mp4",
                "file_version": "video",
                "metadata": "{}",
                "created": "2026-01-01 00:00:00",
                "updated": "2026-01-01 00:00:00",
            },
        )
        self.insert_legacy_row(
            "files",
            {
                "id": legacy_audio_file_id,
                "deleted": None,
                "resource_id": legacy_resource_id,
                "filepath": "legacy/new-audio.mp3",
                "file_version": "audio",
                "metadata": "{}",
                "created": "2026-01-01 00:00:00",
                "updated": "2026-01-01 00:00:00",
            },
        )
        self.insert_legacy_row(
            "contents",
            {
                "id": legacy_content_id,
                "deleted": None,
                "created": "2026-01-01 00:00:00",
                "collection_id": legacy_collection_id,
                "resource_id": legacy_resource_id,
                "title": "Mixed Reuse Lesson",
                "content_type": "video",
                "url": "",
                "description": "",
                "tags": "",
                "annotations": json.dumps([]),
                "thumbnail": "",
                "allow_definitions": 1,
                "allow_notes": 1,
                "allow_captions": 1,
                "views": 5,
                "file_version": "video",
                "published": 1,
                "clips": json.dumps([]),
            },
        )

        migration_request = LegacyMigrationRequest.objects.create(
            requested_by=owner,
            target_owner=owner,
            migration_kind="collection",
            legacy_reference=legacy_collection_id,
        )

        service = self.build_service()
        service.preflight_request(migration_request)
        migration_request.refresh_from_db()

        migration_resource = migration_request.migration_resources.get(
            is_synthetic=False
        )
        file_decisions = list(
            migration_request.file_decisions.order_by("legacy_path").all()
        )

        self.assertIsNone(migration_resource.selected_existing_resource_id)
        self.assertEqual(len(file_decisions), 2)
        self.assertEqual(
            [file_decision.action for file_decision in file_decisions],
            [
                LegacyMigrationFileAction.IMPORT,
                LegacyMigrationFileAction.IMPORT,
            ],
        )
        self.assertEqual(
            [
                file_decision.selected_existing_resource_file_id
                for file_decision in file_decisions
            ],
            [None, None],
        )
        self.assertTrue(
            all(
                file_decision.candidate_matches[0]["match_reason"] == "same_checksum"
                for file_decision in file_decisions
            )
        )
        self.assertEqual(
            migration_request.issues.filter(
                code="duplicate_file_requires_decision",
            ).count(),
            2,
        )
        self.assertFalse(
            migration_request.issues.filter(code="reuse_conflict").exists()
        )

    def test_sync_request_issues_defaults_resource_reuse_target_from_file_reuse(self):
        self.create_legacy_schema()

        owner = UserFactory(netid="profada", username="123456789", instructor=True)
        current_resource = ResourceFactory(
            name="Existing Lecture Resource",
            requester_username=owner.username,
        )
        current_resource_file = ResourceFile(
            resource=current_resource,
            version="english",
            full_video=True,
        )
        self.write_media_file("current/existing-lecture.mp4", payload=b"lecture-bytes")
        current_resource_file.file.name = "current/existing-lecture.mp4"
        current_resource_file.save()

        legacy_collection_id = str(uuid.uuid4())
        legacy_resource_id = str(uuid.uuid4())
        legacy_owner_id = str(uuid.uuid4())
        legacy_file_id = str(uuid.uuid4())
        legacy_content_id = str(uuid.uuid4())
        self.write_media_file("legacy/new-lecture.mp4", payload=b"new-lecture")

        self.insert_legacy_row(
            "users",
            {
                "id": legacy_owner_id,
                "deleted": None,
                "username": "profada",
                "byu_person_id": "123456789",
                "email": "profada@example.test",
            },
        )
        self.insert_legacy_row(
            "collections",
            {
                "id": legacy_collection_id,
                "deleted": None,
                "collection_name": "Manual Reuse Shelf",
                "owner": legacy_owner_id,
                "published": 1,
                "archived": 0,
                "public": 0,
                "copyrighted": 1,
            },
        )
        self.insert_legacy_row(
            "resources",
            {
                "id": legacy_resource_id,
                "deleted": None,
                "resource_name": "Manual Reuse Resource",
                "resource_type": "video",
                "requester_email": "profada@example.test",
                "copyrighted": 1,
                "physical_copy_exists": 0,
                "full_video": 1,
                "published": 1,
                "views": 1,
                "metadata": "{}",
            },
        )
        self.insert_legacy_row(
            "files",
            {
                "id": legacy_file_id,
                "deleted": None,
                "resource_id": legacy_resource_id,
                "filepath": "legacy/new-lecture.mp4",
                "file_version": "english",
                "metadata": "{}",
                "created": "2026-01-01 00:00:00",
                "updated": "2026-01-01 00:00:00",
            },
        )
        self.insert_legacy_row(
            "contents",
            {
                "id": legacy_content_id,
                "deleted": None,
                "created": "2026-01-01 00:00:00",
                "collection_id": legacy_collection_id,
                "resource_id": legacy_resource_id,
                "title": "Manual Reuse Content",
                "content_type": "video",
                "url": "",
                "description": "",
                "tags": "",
                "annotations": json.dumps([]),
                "thumbnail": "",
                "allow_definitions": 1,
                "allow_notes": 1,
                "allow_captions": 1,
                "views": 1,
                "file_version": "english",
                "published": 1,
                "clips": json.dumps([]),
            },
        )

        migration_request = LegacyMigrationRequest.objects.create(
            requested_by=owner,
            target_owner=owner,
            migration_kind="collection",
            legacy_reference=legacy_collection_id,
        )

        service = self.build_service()
        service.preflight_request(migration_request)
        migration_request.refresh_from_db()
        migration_resource = migration_request.migration_resources.get(
            is_synthetic=False
        )
        file_decision = migration_request.file_decisions.get()

        migration_resource.selected_existing_resource = None
        migration_resource.save(
            update_fields=["selected_existing_resource", "updated_at"]
        )
        file_decision.action = LegacyMigrationFileAction.REUSE_EXISTING
        file_decision.selected_existing_resource_file = current_resource_file
        file_decision.save(
            update_fields=[
                "action",
                "selected_existing_resource_file",
                "updated_at",
            ]
        )

        service.sync_request_issues(migration_request)
        migration_resource.refresh_from_db()

        self.assertEqual(
            migration_resource.selected_existing_resource_id,
            current_resource.pk,
        )

    def test_file_decision_form_requires_existing_file_for_reuse(self):
        owner = UserFactory(instructor=True)
        migration_request = LegacyMigrationRequest.objects.create(
            requested_by=owner,
            target_owner=owner,
            migration_kind="resource",
            legacy_reference=str(uuid.uuid4()),
        )
        migration_resource = LegacyMigrationResource.objects.create(
            request=migration_request,
            legacy_resource_id=str(uuid.uuid4()),
            legacy_name="Lecture Resource",
            target_resource_name="Lecture Resource",
        )
        file_decision = LegacyMigrationFileDecision.objects.create(
            request=migration_request,
            migration_resource=migration_resource,
            legacy_file_id=str(uuid.uuid4()),
            legacy_version="english",
            target_version="english",
            legacy_path="legacy/lecture.mp4",
        )

        form = LegacyMigrationFileDecisionForm(
            instance=file_decision,
            data={
                "request": migration_request.pk,
                "migration_resource": migration_resource.pk,
                "legacy_file_id": file_decision.legacy_file_id,
                "legacy_version": "english",
                "target_version": "english",
                "legacy_path": "legacy/lecture.mp4",
                "legacy_extension": "",
                "size_bytes": "",
                "device": "",
                "inode": "",
                "mtime_at": "",
                "atime_at": "",
                "metadata": "{}",
                "linked_contents": "[]",
                "linked_collections": "[]",
                "linked_instructors": "[]",
                "candidate_matches": "[]",
                "checksum": "",
                "action": LegacyMigrationFileAction.REUSE_EXISTING,
                "selected_existing_resource_file": "",
                "notes": "",
            },
        )

        self.assertFalse(form.is_valid())
        self.assertIn(
            "Choose an existing resource file",
            form.non_field_errors()[0],
        )

    def test_import_reuses_selected_existing_resource(self):
        self.create_legacy_schema()
        LanguageFactory(language="English", bcp47="en")

        owner = UserFactory(netid="profada", username="123456789", instructor=True)
        existing_resource = ResourceFactory(
            name="Existing Reused Resource",
            requester_username=owner.username,
        )

        legacy_collection_id = str(uuid.uuid4())
        legacy_resource_id = str(uuid.uuid4())
        legacy_owner_id = str(uuid.uuid4())
        legacy_file_id = str(uuid.uuid4())
        legacy_content_id = str(uuid.uuid4())
        self.write_media_file("legacy/reused-resource.mp4", payload=b"reused-resource")

        self.insert_legacy_row(
            "users",
            {
                "id": legacy_owner_id,
                "deleted": None,
                "username": "profada",
                "byu_person_id": "123456789",
                "email": "profada@example.test",
            },
        )
        self.insert_legacy_row(
            "collections",
            {
                "id": legacy_collection_id,
                "deleted": None,
                "collection_name": "Reuse Existing Resource Shelf",
                "owner": legacy_owner_id,
                "published": 1,
                "archived": 0,
                "public": 0,
                "copyrighted": 1,
            },
        )
        self.insert_legacy_row(
            "resources",
            {
                "id": legacy_resource_id,
                "deleted": None,
                "resource_name": "Legacy Resource That Should Reuse",
                "resource_type": "video",
                "requester_email": "profada@example.test",
                "copyrighted": 1,
                "physical_copy_exists": 0,
                "full_video": 1,
                "published": 1,
                "views": 1,
                "metadata": "{}",
            },
        )
        self.insert_legacy_row(
            "files",
            {
                "id": legacy_file_id,
                "deleted": None,
                "resource_id": legacy_resource_id,
                "filepath": "legacy/reused-resource.mp4",
                "file_version": "english",
                "metadata": "{}",
                "created": "2026-01-01 00:00:00",
                "updated": "2026-01-01 00:00:00",
            },
        )
        self.insert_legacy_row(
            "contents",
            {
                "id": legacy_content_id,
                "deleted": None,
                "created": "2026-01-01 00:00:00",
                "collection_id": legacy_collection_id,
                "resource_id": legacy_resource_id,
                "title": "Reuse Existing Resource Content",
                "content_type": "video",
                "url": "",
                "description": "",
                "tags": "",
                "annotations": json.dumps([]),
                "thumbnail": "",
                "allow_definitions": 1,
                "allow_notes": 1,
                "allow_captions": 1,
                "views": 1,
                "file_version": "english",
                "published": 1,
                "clips": json.dumps([]),
            },
        )

        migration_request = LegacyMigrationRequest.objects.create(
            requested_by=owner,
            target_owner=owner,
            migration_kind="collection",
            legacy_reference=legacy_collection_id,
        )

        service = self.build_service()
        service.preflight_request(migration_request)
        migration_request.refresh_from_db()
        migration_resource = migration_request.migration_resources.get(
            is_synthetic=False
        )
        migration_resource.selected_existing_resource = existing_resource
        migration_resource.save(
            update_fields=["selected_existing_resource", "updated_at"]
        )

        job = service.approve_and_queue_import(migration_request)
        service.run_job(job)

        imported_playlist = Playlist.objects.get(name="Reuse Existing Resource Shelf")
        imported_content = Content.objects.get(
            playlist=imported_playlist,
            title="Reuse Existing Resource Content",
        )

        self.assertEqual(imported_content.resource_id, existing_resource.pk)
        self.assertFalse(
            Resource.objects.filter(name="Legacy Resource That Should Reuse").exists()
        )

    def test_preflight_backfills_target_collection_name_for_existing_target_owner(self):
        self.create_legacy_schema()
        owner = UserFactory(netid="profada", username="123456789", instructor=True)

        legacy_collection_id = str(uuid.uuid4())
        legacy_owner_id = str(uuid.uuid4())
        self.insert_legacy_row(
            "users",
            {
                "id": legacy_owner_id,
                "deleted": None,
                "username": "profada",
                "byu_person_id": "123456789",
                "email": "profada@example.test",
            },
        )
        self.insert_legacy_row(
            "collections",
            {
                "id": legacy_collection_id,
                "deleted": None,
                "collection_name": "Legacy Named Shelf",
                "owner": legacy_owner_id,
                "published": 1,
                "archived": 0,
                "public": 0,
                "copyrighted": 1,
            },
        )

        migration_request = LegacyMigrationRequest.objects.create(
            requested_by=owner,
            target_owner=owner,
            migration_kind="collection",
            legacy_reference=legacy_collection_id,
            target_collection_name="",
        )

        self.build_service().preflight_request(migration_request)
        migration_request.refresh_from_db()

        self.assertEqual(migration_request.target_collection_name, "Legacy Named Shelf")

    def test_preflight_keeps_collection_access_rows_without_matching_legacy_user(self):
        self.create_legacy_schema()
        owner = UserFactory(netid="profada", username="123456789", instructor=True)

        legacy_collection_id = str(uuid.uuid4())
        legacy_owner_id = str(uuid.uuid4())
        self.insert_legacy_row(
            "users",
            {
                "id": legacy_owner_id,
                "deleted": None,
                "username": "profada",
                "byu_person_id": "123456789",
                "email": "profada@example.test",
            },
        )
        self.insert_legacy_row(
            "collections",
            {
                "id": legacy_collection_id,
                "deleted": None,
                "collection_name": "Legacy Access Shelf",
                "owner": legacy_owner_id,
                "published": 1,
                "archived": 0,
                "public": 0,
                "copyrighted": 1,
            },
        )
        self.insert_legacy_row(
            "user_collections_assoc",
            {
                "id": str(uuid.uuid4()),
                "deleted": None,
                "username": "missingguest",
                "collection_id": legacy_collection_id,
                "account_role": PlaylistRole.TA,
            },
        )

        migration_request = LegacyMigrationRequest.objects.create(
            requested_by=owner,
            target_owner=owner,
            migration_kind="collection",
            legacy_reference=legacy_collection_id,
        )

        self.build_service().preflight_request(migration_request)
        migration_request.refresh_from_db()

        self.assertEqual(
            migration_request.raw_snapshot["collection_access"],
            [
                {
                    "collection_id": legacy_collection_id,
                    "account_role": PlaylistRole.TA,
                    "username": "missingguest",
                    "legacy_user_id": None,
                    "byu_person_id": None,
                    "email": None,
                }
            ],
        )
        missing_guest_resolution = migration_request.user_resolutions.get(
            legacy_username="missingguest"
        )
        self.assertEqual(
            missing_guest_resolution.resolution_status,
            LegacyMigrationUserResolutionStatus.PENDING,
        )

    def test_admin_snapshot_previews_show_courses_and_collection_access(self):
        self.create_legacy_schema()
        owner = UserFactory(netid="profada", username="123456789", instructor=True)
        legacy_ta_byu_id = "987654321"
        ta_user = UserFactory(
            netid="caseyta",
            username=legacy_ta_byu_id,
            instructor=True,
        )
        resource_guest = UserFactory(
            netid="resourceg",
            username="333333333",
            instructor=True,
        )

        legacy_collection_id = str(uuid.uuid4())
        legacy_owner_id = str(uuid.uuid4())
        legacy_ta_id = str(uuid.uuid4())
        legacy_course_id = str(uuid.uuid4())
        legacy_resource_id = str(uuid.uuid4())
        legacy_resource_guest_id = str(uuid.uuid4())
        self.insert_legacy_row(
            "users",
            {
                "id": legacy_owner_id,
                "deleted": None,
                "username": "profada",
                "byu_person_id": "123456789",
                "email": "profada@example.test",
            },
        )
        self.insert_legacy_row(
            "users",
            {
                "id": legacy_ta_id,
                "deleted": None,
                "username": ta_user.netid,
                "byu_person_id": legacy_ta_byu_id,
                "email": ta_user.email,
            },
        )
        self.insert_legacy_row(
            "users",
            {
                "id": legacy_resource_guest_id,
                "deleted": None,
                "username": resource_guest.netid,
                "byu_person_id": "333333333",
                "email": resource_guest.email,
            },
        )
        self.insert_legacy_row(
            "collections",
            {
                "id": legacy_collection_id,
                "deleted": None,
                "collection_name": "Legacy Review Shelf",
                "owner": legacy_owner_id,
                "published": 1,
                "archived": 0,
                "public": 1,
                "copyrighted": 1,
            },
        )
        self.insert_legacy_row(
            "user_collections_assoc",
            {
                "id": str(uuid.uuid4()),
                "deleted": None,
                "username": ta_user.netid,
                "collection_id": legacy_collection_id,
                "account_role": PlaylistRole.TA,
            },
        )
        self.insert_legacy_row(
            "courses",
            {
                "id": legacy_course_id,
                "deleted": None,
                "department": "FILM",
                "catalog_number": "101",
                "section_number": "001",
            },
        )
        self.insert_legacy_row(
            "collection_courses_assoc",
            {
                "id": str(uuid.uuid4()),
                "deleted": None,
                "collection_id": legacy_collection_id,
                "course_id": legacy_course_id,
            },
        )
        self.insert_legacy_row(
            "resources",
            {
                "id": legacy_resource_id,
                "deleted": None,
                "resource_name": "Legacy Review Resource",
                "resource_type": "video",
                "requester_email": owner.email,
                "copyrighted": 1,
                "physical_copy_exists": 0,
                "full_video": 1,
                "published": 1,
                "views": 3,
                "metadata": "{}",
            },
        )
        self.insert_legacy_row(
            "resource_access",
            {
                "id": str(uuid.uuid4()),
                "deleted": None,
                "username": resource_guest.netid,
                "resource_id": legacy_resource_id,
            },
        )
        self.insert_legacy_row(
            "files",
            {
                "id": str(uuid.uuid4()),
                "deleted": None,
                "resource_id": legacy_resource_id,
                "filepath": "legacy/review-video.mp4",
                "file_version": "english",
                "metadata": "{}",
                "created": "2026-01-01 00:00:00",
                "updated": "2026-01-01 00:00:00",
            },
        )
        self.write_media_file("legacy/review-video.mp4", payload=b"review-video")
        self.insert_legacy_row(
            "contents",
            {
                "id": str(uuid.uuid4()),
                "deleted": None,
                "created": "2026-01-01 00:00:00",
                "collection_id": legacy_collection_id,
                "resource_id": legacy_resource_id,
                "title": "Legacy Review Content",
                "content_type": "video",
                "url": "",
                "description": "",
                "tags": "",
                "annotations": json.dumps([]),
                "thumbnail": "",
                "allow_definitions": 1,
                "allow_notes": 1,
                "allow_captions": 1,
                "views": 1,
                "file_version": "english",
                "published": 1,
                "clips": json.dumps([]),
            },
        )

        migration_request = LegacyMigrationRequest.objects.create(
            requested_by=owner,
            target_owner=owner,
            migration_kind="collection",
            legacy_reference=legacy_collection_id,
        )

        self.build_service().preflight_request(migration_request)
        migration_request.refresh_from_db()

        admin_instance = LegacyMigrationRequestAdmin(LegacyMigrationRequest, admin.site)
        resource_inline = LegacyMigrationResourceInline(
            LegacyMigrationRequest,
            admin.site,
        )
        access_preview = admin_instance.snapshot_collection_access_preview(
            migration_request
        )
        course_preview = admin_instance.snapshot_courses_preview(migration_request)
        resource_access_preview = resource_inline.resource_access_preview(
            migration_request.migration_resources.get(
                legacy_resource_id=legacy_resource_id
            )
        )

        self.assertIn(ta_user.netid, access_preview)
        self.assertIn(PlaylistRole(PlaylistRole.TA).label, access_preview)
        self.assertIn("FILM 101-001", course_preview)
        self.assertIn(resource_guest.netid, resource_access_preview)

    def test_instructor_request_view_creates_queued_preflight_job(self):
        instructor = UserFactory(instructor=True)
        client = Client()
        client.force_login(
            instructor, backend="django.contrib.auth.backends.ModelBackend"
        )

        response = client.post(
            reverse("create_legacy_migration_request"),
            data={
                "migration_kind": "resource",
                "legacy_reference": str(uuid.uuid4()),
                "request_notes": "Please migrate this resource.",
                "acknowledged_compliance": "on",
                "acknowledged_fair_use_limitation": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        migration_request = LegacyMigrationRequest.objects.get()
        self.assertEqual(migration_request.requested_by, instructor)
        self.assertEqual(migration_request.target_owner, instructor)
        self.assertEqual(migration_request.status, LegacyMigrationStatus.SUBMITTED)
        self.assertTrue(
            migration_request.jobs.filter(
                job_type="preflight", status="queued"
            ).exists()
        )

    def test_lab_assistant_request_view_creates_queued_preflight_job(self):
        lab_assistant = UserFactory(student=True)
        lab_assistant_group, _ = Group.objects.get_or_create(
            name=LAB_ASSISTANT_GROUP_NAME
        )
        lab_assistant.groups.add(lab_assistant_group)
        client = Client()
        client.force_login(
            lab_assistant, backend="django.contrib.auth.backends.ModelBackend"
        )

        response = client.post(
            reverse("create_legacy_migration_request"),
            data={
                "migration_kind": "resource",
                "legacy_reference": str(uuid.uuid4()),
                "request_notes": "Please migrate this resource.",
                "acknowledged_compliance": "on",
                "acknowledged_fair_use_limitation": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        migration_request = LegacyMigrationRequest.objects.get()
        self.assertEqual(migration_request.requested_by, lab_assistant)
        self.assertEqual(migration_request.target_owner, lab_assistant)

    def test_plain_student_request_view_is_forbidden(self):
        student = UserFactory(student=True)
        client = Client()
        client.force_login(student, backend="django.contrib.auth.backends.ModelBackend")

        response = client.post(
            reverse("create_legacy_migration_request"),
            data={
                "migration_kind": "resource",
                "legacy_reference": str(uuid.uuid4()),
                "request_notes": "Please migrate this resource.",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(LegacyMigrationRequest.objects.exists())

    def test_request_page_uses_user_focused_guidance_and_full_width_history(self):
        instructor = UserFactory(instructor=True)
        migration_request = LegacyMigrationRequest.objects.create(
            requested_by=instructor,
            target_owner=instructor,
            migration_kind="collection",
            legacy_reference=str(uuid.uuid4()),
            status=LegacyMigrationStatus.COMPLETED,
        )
        client = Client()
        client.force_login(
            instructor, backend="django.contrib.auth.backends.ModelBackend"
        )

        response = client.get(reverse("legacy_migration_requests"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Move content from the old Y-Video")
        self.assertContains(response, "russell_hansen@byu.edu")
        self.assertContains(response, "801-422-9295")
        self.assertContains(response, "What would you like to move?")
        self.assertContains(response, "legacy-migrations-instructions")
        self.assertContains(response, "legacy-migrations-help-text", count=2)
        self.assertContains(response, "Legal compliance")
        self.assertContains(response, "BYU Visual Teaching Materials Policy")
        self.assertContains(response, "BYU Copyright Policy")
        self.assertContains(response, "BYU's Fair Use checklist")
        self.assertContains(response, "legacy-migrations-submission")
        self.assertContains(
            response,
            '<button type="submit" class="large-button legacy-migrations-submit" disabled>Submit request</button>',
            html=True,
        )
        self.assertContains(response, "js/legacy_migration_requests.js")
        self.assertContains(response, "Your requests")
        self.assertContains(response, "legacy-migrations-table-wrapper")
        self.assertContains(
            response,
            reverse("legacy_migration_request_detail", args=[migration_request.pk]),
        )
        content = response.content.decode()
        self.assertLess(
            content.index("legacy-migrations-form"),
            content.index("legacy-migrations-history"),
        )
        self.assertNotContains(response, "preflight")
        self.assertNotContains(response, "Django admin")

    def test_request_requires_both_legal_compliance_acknowledgements(self):
        instructor = UserFactory(instructor=True)
        client = Client()
        client.force_login(
            instructor, backend="django.contrib.auth.backends.ModelBackend"
        )

        for omitted_field in (
            "acknowledged_compliance",
            "acknowledged_fair_use_limitation",
        ):
            data = {
                "migration_kind": "resource",
                "legacy_reference": str(uuid.uuid4()),
                "request_notes": "Please migrate this resource.",
                "acknowledged_compliance": "on",
                "acknowledged_fair_use_limitation": "on",
            }
            del data[omitted_field]

            with self.subTest(omitted_field=omitted_field):
                response = client.post(
                    reverse("create_legacy_migration_request"), data=data
                )

                self.assertEqual(response.status_code, 400)
                self.assertFalse(LegacyMigrationRequest.objects.exists())
                self.assertIn(omitted_field, response.context["form"].errors)

    @override_settings(LEGACY_MIGRATION_ENABLED=False)
    def test_legacy_migration_views_return_404_when_feature_disabled(self):
        instructor = UserFactory(instructor=True)
        client = Client()
        client.force_login(
            instructor, backend="django.contrib.auth.backends.ModelBackend"
        )

        response = client.get(reverse("legacy_migration_requests"))

        self.assertEqual(response.status_code, 404)

    @override_settings(LEGACY_MIGRATION_ENABLED=False)
    def test_playlists_hides_legacy_migration_link_when_feature_disabled(self):
        instructor = UserFactory(instructor=True)
        client = Client()
        client.force_login(
            instructor, backend="django.contrib.auth.backends.ModelBackend"
        )

        response = client.get(reverse("playlists"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Migrate from legacy")

    def test_admin_preflight_action_reports_legacy_db_error_without_crashing(self):
        admin_user = UserFactory(admin=True)
        migration_request = LegacyMigrationRequest.objects.create(
            requested_by=admin_user,
            target_owner=admin_user,
            migration_kind="collection",
            legacy_reference=str(uuid.uuid4()),
        )
        client = Client()
        client.force_login(
            admin_user, backend="django.contrib.auth.backends.ModelBackend"
        )

        with (
            mock.patch(
                "core.admin_legacy_migration.LegacyMigrationService"
            ) as service_class,
            mock.patch("core.admin_legacy_migration.logger") as logger_mock,
        ):
            service_class.return_value.preflight_request.side_effect = OperationalError(
                "legacy database unavailable"
            )
            response = client.post(
                reverse("admin:core_legacymigrationrequest_changelist"),
                data={
                    "action": "run_preflight_action",
                    "select_across": "0",
                    "index": "0",
                    "_selected_action": [str(migration_request.pk)],
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        admin_messages = list(get_messages(response.wsgi_request))
        self.assertTrue(
            any(
                "Preflight failed for request" in message.message
                for message in admin_messages
            )
        )
        self.assertTrue(
            any(
                message.message == "Preflight failed for 1 request(s)."
                and message.level == messages.ERROR
                for message in admin_messages
            )
        )
        self.assertFalse(
            any(
                "Ran preflight for" in message.message
                and message.level == messages.SUCCESS
                for message in admin_messages
            )
        )
        migration_request.refresh_from_db()
        self.assertEqual(
            migration_request.status, LegacyMigrationStatus.PREFLIGHT_FAILED
        )
        self.assertEqual(
            migration_request.latest_job_error, "legacy database unavailable"
        )
        logger_mock.exception.assert_called_once()

    def test_admin_preflight_action_warns_when_results_are_mixed(self):
        admin_user = UserFactory(admin=True)
        successful_request = LegacyMigrationRequest.objects.create(
            requested_by=admin_user,
            target_owner=admin_user,
            migration_kind="collection",
            legacy_reference=str(uuid.uuid4()),
        )
        failed_request = LegacyMigrationRequest.objects.create(
            requested_by=admin_user,
            target_owner=admin_user,
            migration_kind="collection",
            legacy_reference=str(uuid.uuid4()),
        )
        client = Client()
        client.force_login(
            admin_user, backend="django.contrib.auth.backends.ModelBackend"
        )

        with (
            mock.patch(
                "core.admin_legacy_migration.LegacyMigrationService"
            ) as service_class,
            mock.patch("core.admin_legacy_migration.logger"),
        ):
            service_class.return_value.preflight_request.side_effect = [
                None,
                OperationalError("legacy database unavailable"),
            ]
            response = client.post(
                reverse("admin:core_legacymigrationrequest_changelist"),
                data={
                    "action": "run_preflight_action",
                    "select_across": "0",
                    "index": "0",
                    "_selected_action": [
                        str(successful_request.pk),
                        str(failed_request.pk),
                    ],
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        admin_messages = list(get_messages(response.wsgi_request))
        self.assertTrue(
            any(
                message.message
                == "Ran preflight for 1 request(s). 1 request(s) failed."
                and message.level == messages.WARNING
                for message in admin_messages
            )
        )
        self.assertFalse(
            any(
                message.message == "Ran preflight for 1 request(s)."
                and message.level == messages.SUCCESS
                for message in admin_messages
            )
        )
        successful_request.refresh_from_db()
        failed_request.refresh_from_db()
        failed_requests = [
            migration_request
            for migration_request in (successful_request, failed_request)
            if migration_request.status == LegacyMigrationStatus.PREFLIGHT_FAILED
        ]
        self.assertEqual(len(failed_requests), 1)
        self.assertEqual(
            failed_requests[0].latest_job_error,
            "legacy database unavailable",
        )

    def _change_form_post_data(self, migration_request, **extra):
        data = {
            "requested_by": migration_request.requested_by_id,
            "target_owner": migration_request.target_owner_id,
            "migration_kind": migration_request.migration_kind,
            "legacy_reference": migration_request.legacy_reference,
            "status": migration_request.status,
        }
        for prefix in (
            "migration_resources",
            "file_decisions",
            "user_resolutions",
            "issues",
            "jobs",
            "source_maps",
        ):
            data[f"{prefix}-TOTAL_FORMS"] = "0"
            data[f"{prefix}-INITIAL_FORMS"] = "0"
            data[f"{prefix}-MIN_NUM_FORMS"] = "0"
            data[f"{prefix}-MAX_NUM_FORMS"] = "1000"
        data.update(extra)
        return data

    def test_change_form_shows_action_buttons_only_when_editing(self):
        admin_user = UserFactory(admin=True)
        migration_request = LegacyMigrationRequest.objects.create(
            requested_by=admin_user,
            target_owner=admin_user,
            migration_kind="collection",
            legacy_reference=str(uuid.uuid4()),
        )
        client = Client()
        client.force_login(
            admin_user, backend="django.contrib.auth.backends.ModelBackend"
        )

        change_response = client.get(
            reverse(
                "admin:core_legacymigrationrequest_change", args=[migration_request.pk]
            )
        )
        for button_name in (
            "_run_preflight",
            "_refresh_issues",
            "_approve_and_queue",
            "_retry_latest_failed_job",
            "_cancel_jobs",
        ):
            self.assertIn(f'name="{button_name}"'.encode(), change_response.content)

        add_response = client.get(reverse("admin:core_legacymigrationrequest_add"))
        self.assertNotIn(b'name="_run_preflight"', add_response.content)

    def test_change_form_run_preflight_button_runs_preflight(self):
        admin_user = UserFactory(admin=True)
        migration_request = LegacyMigrationRequest.objects.create(
            requested_by=admin_user,
            target_owner=admin_user,
            migration_kind="collection",
            legacy_reference=str(uuid.uuid4()),
        )
        client = Client()
        client.force_login(
            admin_user, backend="django.contrib.auth.backends.ModelBackend"
        )

        with mock.patch(
            "core.admin_legacy_migration.LegacyMigrationService"
        ) as service_class:
            response = client.post(
                reverse(
                    "admin:core_legacymigrationrequest_change",
                    args=[migration_request.pk],
                ),
                data=self._change_form_post_data(
                    migration_request, _run_preflight="Run preflight"
                ),
                follow=True,
            )

        service_class.return_value.preflight_request.assert_called_once_with(
            migration_request
        )
        self.assertEqual(response.status_code, 200)
        admin_messages = list(get_messages(response.wsgi_request))
        self.assertTrue(
            any(
                message.message
                == f"Ran preflight for request {migration_request.request_uuid}."
                and message.level == messages.SUCCESS
                for message in admin_messages
            )
        )

    def test_change_form_run_preflight_button_reports_error(self):
        admin_user = UserFactory(admin=True)
        migration_request = LegacyMigrationRequest.objects.create(
            requested_by=admin_user,
            target_owner=admin_user,
            migration_kind="collection",
            legacy_reference=str(uuid.uuid4()),
        )
        client = Client()
        client.force_login(
            admin_user, backend="django.contrib.auth.backends.ModelBackend"
        )

        with mock.patch(
            "core.admin_legacy_migration.LegacyMigrationService"
        ) as service_class:
            service_class.return_value.preflight_request.side_effect = OperationalError(
                "legacy database unavailable"
            )
            response = client.post(
                reverse(
                    "admin:core_legacymigrationrequest_change",
                    args=[migration_request.pk],
                ),
                data=self._change_form_post_data(
                    migration_request, _run_preflight="Run preflight"
                ),
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        admin_messages = list(get_messages(response.wsgi_request))
        self.assertTrue(
            any(
                message.message
                == (
                    f"Preflight failed for request {migration_request.request_uuid}: "
                    "legacy database unavailable"
                )
                and message.level == messages.ERROR
                for message in admin_messages
            )
        )
        migration_request.refresh_from_db()
        self.assertEqual(
            migration_request.status, LegacyMigrationStatus.PREFLIGHT_FAILED
        )

    def test_change_form_refresh_issues_button(self):
        admin_user = UserFactory(admin=True)
        migration_request = LegacyMigrationRequest.objects.create(
            requested_by=admin_user,
            target_owner=admin_user,
            migration_kind="collection",
            legacy_reference=str(uuid.uuid4()),
        )
        client = Client()
        client.force_login(
            admin_user, backend="django.contrib.auth.backends.ModelBackend"
        )

        with mock.patch(
            "core.admin_legacy_migration.LegacyMigrationService"
        ) as service_class:
            response = client.post(
                reverse(
                    "admin:core_legacymigrationrequest_change",
                    args=[migration_request.pk],
                ),
                data=self._change_form_post_data(
                    migration_request, _refresh_issues="Refresh issues"
                ),
                follow=True,
            )

        # save_related() already calls sync_request_issues() on every save
        # (see LegacyMigrationRequestAdmin.save_related), so the button's own
        # call is the second one, not the only one.
        self.assertEqual(service_class.return_value.sync_request_issues.call_count, 2)
        admin_messages = list(get_messages(response.wsgi_request))
        self.assertTrue(
            any(
                message.message
                == f"Refreshed issues for request {migration_request.request_uuid}."
                and message.level == messages.SUCCESS
                for message in admin_messages
            )
        )

    def test_change_form_approve_and_queue_button_success(self):
        admin_user = UserFactory(admin=True)
        migration_request = LegacyMigrationRequest.objects.create(
            requested_by=admin_user,
            target_owner=admin_user,
            migration_kind="collection",
            legacy_reference=str(uuid.uuid4()),
        )
        client = Client()
        client.force_login(
            admin_user, backend="django.contrib.auth.backends.ModelBackend"
        )

        with mock.patch(
            "core.admin_legacy_migration.LegacyMigrationService"
        ) as service_class:
            response = client.post(
                reverse(
                    "admin:core_legacymigrationrequest_change",
                    args=[migration_request.pk],
                ),
                data=self._change_form_post_data(
                    migration_request, _approve_and_queue="Approve and queue import"
                ),
                follow=True,
            )

        service_class.return_value.approve_and_queue_import.assert_called_once_with(
            migration_request
        )
        admin_messages = list(get_messages(response.wsgi_request))
        self.assertTrue(
            any(
                message.message
                == (
                    f"Approved and queued request {migration_request.request_uuid} "
                    "for import."
                )
                and message.level == messages.SUCCESS
                for message in admin_messages
            )
        )

    def test_change_form_approve_and_queue_button_reports_blocking_issues(self):
        admin_user = UserFactory(admin=True)
        migration_request = LegacyMigrationRequest.objects.create(
            requested_by=admin_user,
            target_owner=admin_user,
            migration_kind="collection",
            legacy_reference=str(uuid.uuid4()),
        )
        client = Client()
        client.force_login(
            admin_user, backend="django.contrib.auth.backends.ModelBackend"
        )

        with mock.patch(
            "core.admin_legacy_migration.LegacyMigrationService"
        ) as service_class:
            service_class.return_value.approve_and_queue_import.side_effect = (
                ValueError("The migration request still has blocking issues.")
            )
            response = client.post(
                reverse(
                    "admin:core_legacymigrationrequest_change",
                    args=[migration_request.pk],
                ),
                data=self._change_form_post_data(
                    migration_request, _approve_and_queue="Approve and queue import"
                ),
                follow=True,
            )

        admin_messages = list(get_messages(response.wsgi_request))
        self.assertTrue(
            any(
                message.message
                == (
                    f"Approval failed for request {migration_request.request_uuid}: "
                    "The migration request still has blocking issues."
                )
                and message.level == messages.ERROR
                for message in admin_messages
            )
        )

    def test_approve_and_queue_import_requires_preflight(self):
        admin_user = UserFactory(admin=True)
        migration_request = LegacyMigrationRequest.objects.create(
            requested_by=admin_user,
            target_owner=admin_user,
            migration_kind="collection",
            legacy_reference=str(uuid.uuid4()),
        )
        service = self.build_service()
        with self.assertRaisesMessage(
            ValueError,
            "Run preflight before approving this request for import.",
        ):
            service.approve_and_queue_import(migration_request)

    def test_change_form_approve_button_disabled_before_preflight(self):
        admin_user = UserFactory(admin=True)
        migration_request = LegacyMigrationRequest.objects.create(
            requested_by=admin_user,
            target_owner=admin_user,
            migration_kind="collection",
            legacy_reference=str(uuid.uuid4()),
        )
        client = Client()
        client.force_login(
            admin_user, backend="django.contrib.auth.backends.ModelBackend"
        )

        change_url = reverse(
            "admin:core_legacymigrationrequest_change", args=[migration_request.pk]
        )
        response = client.get(change_url)
        self.assertIn(b'name="_approve_and_queue" disabled', response.content)

        migration_request.preflight_completed_at = timezone.now()
        migration_request.save(update_fields=["preflight_completed_at"])

        response = client.get(change_url)
        self.assertNotIn(b'name="_approve_and_queue" disabled', response.content)

    def test_status_display_shows_active_job_type(self):
        admin_user = UserFactory(admin=True)
        migration_request = LegacyMigrationRequest.objects.create(
            requested_by=admin_user,
            target_owner=admin_user,
            migration_kind="collection",
            legacy_reference=str(uuid.uuid4()),
            status=LegacyMigrationStatus.RUNNING,
        )
        migration_request.jobs.create(
            job_type=LegacyMigrationJobType.IMPORT,
            status=LegacyMigrationJobStatus.RUNNING,
        )

        model_admin = LegacyMigrationRequestAdmin(LegacyMigrationRequest, admin.site)
        self.assertEqual(
            model_admin.status_display(migration_request), "Running (Import)"
        )

        active_job_summary = model_admin.active_job_summary(migration_request)
        self.assertIn("Import job is running", active_job_summary)

    def test_change_form_retry_latest_failed_job_button_no_failed_job(self):
        admin_user = UserFactory(admin=True)
        migration_request = LegacyMigrationRequest.objects.create(
            requested_by=admin_user,
            target_owner=admin_user,
            migration_kind="collection",
            legacy_reference=str(uuid.uuid4()),
        )
        client = Client()
        client.force_login(
            admin_user, backend="django.contrib.auth.backends.ModelBackend"
        )

        response = client.post(
            reverse(
                "admin:core_legacymigrationrequest_change",
                args=[migration_request.pk],
            ),
            data=self._change_form_post_data(
                migration_request,
                _retry_latest_failed_job="Retry latest failed job",
            ),
            follow=True,
        )

        admin_messages = list(get_messages(response.wsgi_request))
        self.assertTrue(
            any(
                message.message
                == (
                    "No failed job to retry for request "
                    f"{migration_request.request_uuid}."
                )
                and message.level == messages.INFO
                for message in admin_messages
            )
        )

    def test_change_form_retry_latest_failed_job_button_success(self):
        admin_user = UserFactory(admin=True)
        migration_request = LegacyMigrationRequest.objects.create(
            requested_by=admin_user,
            target_owner=admin_user,
            migration_kind="collection",
            legacy_reference=str(uuid.uuid4()),
        )
        migration_request.jobs.create(job_type="import", status="failed")
        client = Client()
        client.force_login(
            admin_user, backend="django.contrib.auth.backends.ModelBackend"
        )

        response = client.post(
            reverse(
                "admin:core_legacymigrationrequest_change",
                args=[migration_request.pk],
            ),
            data=self._change_form_post_data(
                migration_request,
                _retry_latest_failed_job="Retry latest failed job",
            ),
            follow=True,
        )

        migration_request.refresh_from_db()
        self.assertEqual(migration_request.status, LegacyMigrationStatus.QUEUED)
        self.assertEqual(migration_request.jobs.filter(status="queued").count(), 1)
        admin_messages = list(get_messages(response.wsgi_request))
        self.assertTrue(
            any(
                message.message
                == f"Queued a retry for request {migration_request.request_uuid}."
                and message.level == messages.SUCCESS
                for message in admin_messages
            )
        )

    def test_change_form_cancel_jobs_button(self):
        admin_user = UserFactory(admin=True)
        migration_request = LegacyMigrationRequest.objects.create(
            requested_by=admin_user,
            target_owner=admin_user,
            migration_kind="collection",
            legacy_reference=str(uuid.uuid4()),
        )
        migration_request.jobs.create(job_type="import", status="queued")
        migration_request.jobs.create(job_type="import", status="running")
        client = Client()
        client.force_login(
            admin_user, backend="django.contrib.auth.backends.ModelBackend"
        )

        response = client.post(
            reverse(
                "admin:core_legacymigrationrequest_change",
                args=[migration_request.pk],
            ),
            data=self._change_form_post_data(
                migration_request, _cancel_jobs="Cancel queued/running jobs"
            ),
            follow=True,
        )

        migration_request.refresh_from_db()
        self.assertEqual(migration_request.status, LegacyMigrationStatus.CANCELED)
        self.assertEqual(migration_request.jobs.filter(status="canceled").count(), 2)
        admin_messages = list(get_messages(response.wsgi_request))
        self.assertTrue(
            any(
                message.message
                == (
                    "Canceled queued/running jobs for request "
                    f"{migration_request.request_uuid}."
                )
                and message.level == messages.SUCCESS
                for message in admin_messages
            )
        )

    def test_change_form_plain_save_does_not_trigger_actions(self):
        admin_user = UserFactory(admin=True)
        migration_request = LegacyMigrationRequest.objects.create(
            requested_by=admin_user,
            target_owner=admin_user,
            migration_kind="collection",
            legacy_reference=str(uuid.uuid4()),
        )
        client = Client()
        client.force_login(
            admin_user, backend="django.contrib.auth.backends.ModelBackend"
        )

        with mock.patch(
            "core.admin_legacy_migration.LegacyMigrationService"
        ) as service_class:
            response = client.post(
                reverse(
                    "admin:core_legacymigrationrequest_change",
                    args=[migration_request.pk],
                ),
                data=self._change_form_post_data(
                    migration_request, _continue="Save and continue editing"
                ),
                follow=True,
            )

        service_class.return_value.preflight_request.assert_not_called()
        service_class.return_value.approve_and_queue_import.assert_not_called()
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            reverse(
                "admin:core_legacymigrationrequest_change",
                args=[migration_request.pk],
            ),
            [url for url, _ in response.redirect_chain],
        )

    @override_settings(LEGACY_MIGRATION_CREATE_MISSING_USERS=True)
    def test_upsert_user_resolution_handles_missing_autocreate_user(self):
        owner = UserFactory(netid="profada", username="123456789", instructor=True)
        migration_request = LegacyMigrationRequest.objects.create(
            requested_by=owner,
            target_owner=owner,
            migration_kind="collection",
            legacy_reference=str(uuid.uuid4()),
        )
        service = self.build_service()

        with mock.patch(
            "yvideo.odhOIDCAuthenticationBackend.OIDCUserAuth.create_user",
            return_value=None,
        ):
            resolution = service._upsert_user_resolution(
                migration_request,
                {
                    "legacy_user_id": "legacy-user-1",
                    "legacy_username": "",
                    "legacy_byu_id": "555555555",
                    "legacy_email": "",
                },
                "collection_owner",
                "collection:demo",
            )

        self.assertIsNone(resolution.matched_user)
        self.assertEqual(
            resolution.resolution_status,
            LegacyMigrationUserResolutionStatus.PENDING,
        )

    @override_settings(LEGACY_MIGRATION_CREATE_MISSING_USERS=True)
    def test_upsert_user_resolution_resolves_serialized_autocreate_user(self):
        owner = UserFactory(netid="profada", username="123456789", instructor=True)
        created_user = UserFactory(
            netid="rjr45",
            username="555555555",
            first_name="Rob",
            last_name="Reynolds",
            instructor=True,
        )
        migration_request = LegacyMigrationRequest.objects.create(
            requested_by=owner,
            target_owner=owner,
            migration_kind="collection",
            legacy_reference=str(uuid.uuid4()),
        )
        service = self.build_service()

        with (
            mock.patch(
                "yvideo.odhOIDCAuthenticationBackend.OIDCUserAuth.create_user",
                return_value=created_user,
            ),
            mock.patch("core.model_utils.update_user_enrollment"),
        ):
            resolution = service._upsert_user_resolution(
                migration_request,
                {
                    "legacy_user_id": "legacy-user-2",
                    "legacy_username": "",
                    "legacy_byu_id": "555555555",
                    "legacy_email": "",
                },
                "collection_owner",
                "collection:demo",
            )

        self.assertEqual(resolution.matched_user, created_user)
        self.assertEqual(
            resolution.resolution_status,
            LegacyMigrationUserResolutionStatus.AUTO,
        )

    @override_settings(LEGACY_MIGRATION_MEDIA_ROOT="yvideo:/opt/media/y-video")
    def test_get_legacy_file_info_reads_remote_paths_over_ssh(self):
        service = self.build_service()

        with mock.patch(
            "core.legacy_migration.remote_files.subprocess.run"
        ) as run_mock:
            run_mock.return_value = mock.Mock(
                stdout=(
                    "12\t1700000000\t1700000100\t"
                    "/opt/media/y-video/legacy/shared-birds.mp4\n"
                ),
                stderr="",
            )
            file_info = service._get_legacy_file_info(
                {"filepath": "legacy/shared-birds.mp4"}
            )

        self.assertEqual(
            file_info.absolute_path,
            "yvideo:/opt/media/y-video/legacy/shared-birds.mp4",
        )
        self.assertEqual(
            file_info.realpath,
            "yvideo:/opt/media/y-video/legacy/shared-birds.mp4",
        )
        self.assertEqual(file_info.size_bytes, 12)
        self.assertIsNone(file_info.device)
        self.assertIsNone(file_info.inode)
        self.assertEqual(file_info.extension, ".mp4")
        self.assertEqual(
            run_mock.call_args.args[0][:3],
            ["ssh", "-oBatchMode=yes", "yvideo"],
        )

    @override_settings(LEGACY_MIGRATION_MEDIA_ROOT="yvideo:/opt/media/y-video")
    def test_get_legacy_file_info_parses_remote_metadata_with_literal_tab_escapes(self):
        service = self.build_service()

        with mock.patch(
            "core.legacy_migration.remote_files.subprocess.run"
        ) as run_mock:
            run_mock.return_value = mock.Mock(
                stdout=(
                    "998149\\t1697125681\\t1776149000\n"
                    "\t/opt/media/y-video/legacy/shared-birds.mp4\n"
                ),
                stderr="",
            )
            file_info = service._get_legacy_file_info(
                {"filepath": "legacy/shared-birds.mp4"}
            )

        self.assertEqual(file_info.size_bytes, 998149)
        self.assertEqual(
            file_info.realpath,
            "yvideo:/opt/media/y-video/legacy/shared-birds.mp4",
        )
        self.assertEqual(
            run_mock.call_args.args[0][:3],
            ["ssh", "-oBatchMode=yes", "yvideo"],
        )

    @override_settings(LEGACY_MIGRATION_MEDIA_ROOT="yvideo:/opt/media/y-video")
    def test_get_legacy_file_info_logs_remote_command_failure_details(self):
        service = self.build_service()
        failure = subprocess.CalledProcessError(
            returncode=1,
            cmd=["ssh", "-oBatchMode=yes", "yvideo", "stat"],
            output="",
            stderr=(
                "stat: cannot stat '/opt/media/y-video/legacy/shared-birds.mp4': "
                "No such file or directory"
            ),
        )

        with (
            mock.patch(
                "core.legacy_migration.remote_files.subprocess.run",
                side_effect=failure,
            ),
            self.assertLogs("core.legacy_migration.service", level="WARNING") as logs,
        ):
            file_info = service._get_legacy_file_info(
                {"filepath": "legacy/shared-birds.mp4"}
            )

        self.assertIsNone(file_info.size_bytes)
        self.assertIn(
            "Could not inspect remote legacy file", file_info.inspection_error
        )
        self.assertIn(
            "Command: ssh -oBatchMode=yes yvideo",
            file_info.inspection_error,
        )
        self.assertIn(
            "stderr: stat: cannot stat '/opt/media/y-video/legacy/shared-birds.mp4'",
            file_info.inspection_error,
        )
        self.assertIn(
            "Legacy file inspection failed for "
            "yvideo:/opt/media/y-video/legacy/shared-birds.mp4:",
            logs.output[0],
        )

    def test_missing_legacy_file_issue_includes_inspection_error_details(self):
        owner = UserFactory(netid="profada", instructor=True)
        migration_request = LegacyMigrationRequest.objects.create(
            requested_by=owner,
            target_owner=owner,
            migration_kind="collection",
            legacy_reference=str(uuid.uuid4()),
        )
        migration_resource = LegacyMigrationResource.objects.create(
            request=migration_request,
            legacy_resource_id=str(uuid.uuid4()),
            legacy_name="Legacy Video",
            target_resource_name="Legacy Video",
        )
        file_decision = LegacyMigrationFileDecision.objects.create(
            request=migration_request,
            migration_resource=migration_resource,
            legacy_file_id=str(uuid.uuid4()),
            legacy_version="english",
            legacy_path="legacy/shared-birds.mp4",
            metadata={
                "inspection_error": (
                    "Could not inspect remote legacy file "
                    "yvideo:/opt/media/y-video/legacy/shared-birds.mp4. "
                    "Command: ssh -oBatchMode=yes yvideo 'stat ...'. "
                    "Exit status: 1. stderr: Permission denied"
                )
            },
        )

        LegacyMigrationService(require_catalog=False).sync_request_issues(
            migration_request
        )

        issue = migration_request.issues.get(
            code="missing_legacy_file",
            file_decision=file_decision,
        )
        self.assertEqual(
            issue.message,
            "The legacy file could not be inspected during preflight. "
            "See details for the failing command.",
        )
        self.assertEqual(issue.details["path"], "legacy/shared-birds.mp4")
        self.assertIn(
            "Command: ssh -oBatchMode=yes yvideo", issue.details["inspection_error"]
        )

    def test_remote_legacy_checksum_streams_over_ssh(self):
        checksum_cache = ChecksumCache()
        process = mock.MagicMock()
        process.stdout = io.BytesIO(b"legacy-video")
        process.stderr = io.BytesIO(b"")
        process.wait.return_value = 0
        process.__enter__.return_value = process

        with mock.patch(
            "core.legacy_migration.remote_files.subprocess.Popen",
            return_value=process,
        ) as popen_mock:
            checksum = checksum_cache.get_or_compute_legacy_checksum(
                LegacyFileInfo(
                    absolute_path="yvideo:/opt/media/y-video/legacy/shared-birds.mp4",
                    size_bytes=12,
                    mtime_ns=1700000000000000000,
                )
            )

        self.assertEqual(checksum, xxhash.xxh64(b"legacy-video").hexdigest())
        self.assertEqual(
            popen_mock.call_args.args[0][:3],
            ["ssh", "-oBatchMode=yes", "yvideo"],
        )

    def test_import_file_to_storage_uses_scp_for_remote_source(self):
        service = self.build_service()
        resource = ResourceFactory(name="Imported Lecture")
        source_path = "yvideo:/opt/media/y-video/legacy dir/imported.mp4"

        with mock.patch(
            "core.legacy_migration.remote_files.subprocess.run"
        ) as run_mock:
            relative_name = service._import_file_to_storage(
                source_path,
                resource,
                "english",
            )

        destination = Path(settings.MEDIA_ROOT) / relative_name
        self.assertEqual(relative_name, "Imported Lecture/english.mp4")
        self.assertTrue(destination.parent.exists())
        run_mock.assert_called_once_with(
            [
                "scp",
                "-p",
                "-oBatchMode=yes",
                "yvideo:'/opt/media/y-video/legacy dir/imported.mp4'",
                str(destination),
            ],
            capture_output=True,
            text=True,
            check=True,
        )

    def test_import_annotations_handles_all_legacy_types(self):
        service = LegacyMigrationService(require_catalog=False)
        owner = UserFactory(instructor=True)
        migration_request = LegacyMigrationRequest.objects.create(
            requested_by=owner,
            target_owner=owner,
            migration_kind="collection",
            legacy_reference=str(uuid.uuid4()),
        )
        content = ContentFactory()
        legacy_annotations = [
            {"type": "Pause", "layer": 0, "start": 5, "message": "stop here"},
            {"type": "Skip", "layer": 0, "start": 1, "end": 2},
            {"type": "Mute", "layer": 1, "start": 3, "end": 4},
            {"type": "Blank", "layer": 1, "start": 6, "end": 7},
            {"type": "Mystery", "layer": 9, "start": 0, "end": 1},
        ]

        annotation_set = service._import_annotations(
            migration_request, content, legacy_annotations
        )

        pause = PauseAnnotation.objects.get(track__annotation_set=annotation_set)
        self.assertEqual(pause.start_time, 5.0)
        self.assertEqual(pause.end_time, 5.0)
        self.assertEqual(pause.message, "stop here")
        self.assertEqual(
            SkipAnnotation.objects.filter(track__annotation_set=annotation_set).count(),
            1,
        )
        self.assertEqual(
            MuteAnnotation.objects.filter(track__annotation_set=annotation_set).count(),
            1,
        )
        self.assertEqual(
            BlankAnnotation.objects.filter(
                track__annotation_set=annotation_set
            ).count(),
            1,
        )
        # The unrecognized "Mystery" event must not leave an empty track behind.
        self.assertEqual(annotation_set.tracks.count(), 2)

    def test_sync_issues_allows_retry_when_collection_was_created_by_this_request(
        self,
    ):
        service = LegacyMigrationService(require_catalog=False)
        owner = UserFactory(instructor=True)
        legacy_collection_id = str(uuid.uuid4())
        migration_request = LegacyMigrationRequest.objects.create(
            requested_by=owner,
            target_owner=owner,
            migration_kind="collection",
            legacy_reference=legacy_collection_id,
            target_collection_name="Imported Shelf",
            raw_snapshot={
                "collection": {
                    "legacy_collection_id": legacy_collection_id,
                    "name": "Imported Shelf",
                },
            },
        )
        playlist = PlaylistFactory(owner=owner, name="Imported Shelf")

        service.sync_request_issues(migration_request)
        self.assertTrue(
            migration_request.issues.filter(code="playlist_name_conflict").exists()
        )

        LegacySourceMap.objects.create(
            source_type="collection",
            source_id=legacy_collection_id,
            request=migration_request,
            target_model="Playlist",
            target_id=playlist.pk,
        )
        service.sync_request_issues(migration_request)
        self.assertFalse(
            migration_request.issues.filter(code="playlist_name_conflict").exists()
        )

    def test_sync_issues_warns_on_unknown_collection_role(self):
        service = LegacyMigrationService(require_catalog=False)
        owner = UserFactory(instructor=True)
        migration_request = LegacyMigrationRequest.objects.create(
            requested_by=owner,
            target_owner=owner,
            migration_kind="collection",
            legacy_reference=str(uuid.uuid4()),
            raw_snapshot={
                "collection_access": [
                    {
                        "legacy_user_id": "",
                        "username": "mystery",
                        "byu_person_id": "",
                        "email": "",
                        "account_role": 99,
                        "collection_id": "c1",
                    }
                ],
            },
        )

        service.sync_request_issues(migration_request)

        issue = migration_request.issues.get(code="unknown_collection_role")
        self.assertEqual(issue.severity, LegacyMigrationIssueSeverity.WARNING)
        self.assertEqual(issue.details["username"], "mystery")

    def test_a_legacy_auditor_is_recognized_as_a_student(self):
        """PlaylistRole dropped AUDITOR (#361), but role 3 is not unknown.

        An unrecognized role is skipped outright at import, so treating the legacy
        auditor as unknown would silently drop the grant instead of downgrading it.
        """
        service = LegacyMigrationService(require_catalog=False)
        owner = UserFactory(instructor=True)
        migration_request = LegacyMigrationRequest.objects.create(
            requested_by=owner,
            target_owner=owner,
            migration_kind="collection",
            legacy_reference=str(uuid.uuid4()),
            raw_snapshot={
                "collection_access": [
                    {
                        "legacy_user_id": "",
                        "username": "auditor",
                        "byu_person_id": "",
                        "email": "",
                        "account_role": 3,
                        "collection_id": "c1",
                    }
                ],
            },
        )

        service.sync_request_issues(migration_request)

        self.assertFalse(
            migration_request.issues.filter(code="unknown_collection_role").exists()
        )
        self.assertEqual(
            service._resolve_collection_role(3),
            PlaylistRole.STUDENT,
        )

    def test_claim_job_is_atomic(self):
        service = LegacyMigrationService(require_catalog=False)
        owner = UserFactory(instructor=True)
        migration_request = LegacyMigrationRequest.objects.create(
            requested_by=owner,
            target_owner=owner,
            migration_kind="collection",
            legacy_reference=str(uuid.uuid4()),
        )
        job = migration_request.queue_job("import")

        self.assertTrue(service._claim_job(job))
        self.assertFalse(service._claim_job(job))

    def test_running_import_stops_at_phase_boundary_when_canceled(self):
        service = LegacyMigrationService(require_catalog=False)
        owner = UserFactory(instructor=True)
        migration_request = LegacyMigrationRequest.objects.create(
            requested_by=owner,
            target_owner=owner,
            migration_kind="collection",
            legacy_reference=str(uuid.uuid4()),
        )
        job = migration_request.queue_job("import")
        LegacyMigrationJob.objects.filter(pk=job.pk).update(status="canceled")

        with self.assertRaises(LegacyMigrationJobCanceled):
            service._log_job_phase(job, "files")

    def test_run_job_marks_request_canceled_when_import_is_canceled(self):
        service = LegacyMigrationService(require_catalog=False)
        owner = UserFactory(instructor=True)
        migration_request = LegacyMigrationRequest.objects.create(
            requested_by=owner,
            target_owner=owner,
            migration_kind="collection",
            legacy_reference=str(uuid.uuid4()),
        )
        job = migration_request.queue_job("import")

        with mock.patch.object(
            LegacyMigrationService,
            "import_request",
            side_effect=LegacyMigrationJobCanceled("canceled"),
        ):
            service.run_job(job)

        job.refresh_from_db()
        migration_request.refresh_from_db()
        self.assertEqual(job.status, "canceled")
        self.assertEqual(migration_request.status, LegacyMigrationStatus.CANCELED)

    def test_import_file_to_storage_does_not_clobber_referenced_media(self):
        service = LegacyMigrationService(require_catalog=False)
        resource = ResourceFactory(name="Shared Name")
        existing_path = self.write_media_file(
            "Shared Name/english.mp4", payload=b"existing-bytes"
        )
        existing_file = ResourceFile(
            resource=resource, version="english", full_video=True
        )
        existing_file.file.name = "Shared Name/english.mp4"
        existing_file.save()
        source_path = self.write_media_file(
            "legacy/new-video.mp4", payload=b"new-bytes"
        )

        relative_name = service._import_file_to_storage(
            str(source_path), resource, "english"
        )

        self.assertNotEqual(relative_name, "Shared Name/english.mp4")
        self.assertEqual(existing_path.read_bytes(), b"existing-bytes")
        self.assertEqual(
            (Path(settings.MEDIA_ROOT) / relative_name).read_bytes(), b"new-bytes"
        )

    def test_import_file_to_storage_replaces_stale_partial_copy(self):
        service = LegacyMigrationService(require_catalog=False)
        resource = ResourceFactory(name="Stale Name")
        self.write_media_file("Stale Name/english.mp4", payload=b"stale-bytes")
        source_path = self.write_media_file("legacy/fresh.mp4", payload=b"fresh-bytes")

        relative_name = service._import_file_to_storage(
            str(source_path), resource, "english"
        )

        self.assertEqual(relative_name, "Stale Name/english.mp4")
        self.assertEqual(
            (Path(settings.MEDIA_ROOT) / relative_name).read_bytes(), b"fresh-bytes"
        )

    def test_import_shares_identical_files_within_request(self):
        self.create_legacy_schema()
        target_owner = UserFactory(
            netid="profdup", username="444444444", instructor=True
        )

        legacy_collection_id = str(uuid.uuid4())
        legacy_owner_id = str(uuid.uuid4())
        resource_a_id = str(uuid.uuid4())
        resource_b_id = str(uuid.uuid4())
        self.write_media_file("legacy/dup-a.mp4", payload=b"identical-bytes")
        self.write_media_file("legacy/dup-b.mp4", payload=b"identical-bytes")

        self.insert_legacy_row(
            "users",
            {
                "id": legacy_owner_id,
                "deleted": None,
                "username": "profdup",
                "byu_person_id": "444444444",
                "email": "profdup@example.test",
            },
        )
        self.insert_legacy_row(
            "collections",
            {
                "id": legacy_collection_id,
                "deleted": None,
                "collection_name": "Duplicate Files Shelf",
                "owner": legacy_owner_id,
                "published": 1,
                "archived": 0,
                "public": 0,
                "copyrighted": 1,
            },
        )
        for resource_id, resource_name, file_path in (
            (resource_a_id, "Duplicate Alpha", "legacy/dup-a.mp4"),
            (resource_b_id, "Duplicate Beta", "legacy/dup-b.mp4"),
        ):
            self.insert_legacy_row(
                "resources",
                {
                    "id": resource_id,
                    "deleted": None,
                    "resource_name": resource_name,
                    "resource_type": "video",
                    "requester_email": "profdup@example.test",
                    "copyrighted": 1,
                    "physical_copy_exists": 0,
                    "full_video": 1,
                    "published": 1,
                    "views": 0,
                    "metadata": "",
                },
            )
            self.insert_legacy_row(
                "files",
                {
                    "id": str(uuid.uuid4()),
                    "deleted": None,
                    "resource_id": resource_id,
                    "filepath": file_path,
                    "file_version": "english",
                    "metadata": "",
                    "created": "2026-01-01 00:00:00",
                    "updated": "2026-01-01 00:00:00",
                },
            )
            self.insert_legacy_row(
                "contents",
                {
                    "id": str(uuid.uuid4()),
                    "deleted": None,
                    "created": "2026-01-01 00:00:00",
                    "collection_id": legacy_collection_id,
                    "resource_id": resource_id,
                    "title": f"Content for {resource_name}",
                    "content_type": "video",
                    "url": "",
                    "description": "",
                    "tags": "",
                    "annotations": "[]",
                    "thumbnail": "",
                    "allow_definitions": 1,
                    "allow_notes": 1,
                    "allow_captions": 1,
                    "views": 0,
                    "file_version": "english",
                    "published": 1,
                    "clips": "[]",
                },
            )

        migration_request = LegacyMigrationRequest.objects.create(
            requested_by=target_owner,
            target_owner=target_owner,
            migration_kind="collection",
            legacy_reference=legacy_collection_id,
        )
        service = self.build_service()
        service.preflight_request(migration_request)
        migration_request.refresh_from_db()

        checksums = set(
            migration_request.file_decisions.values_list("checksum", flat=True)
        )
        self.assertEqual(len(checksums), 1)
        self.assertNotIn("", checksums)
        duplicate_issue = migration_request.issues.get(code="duplicate_file_in_request")
        self.assertEqual(duplicate_issue.severity, LegacyMigrationIssueSeverity.WARNING)
        self.assertFalse(migration_request.has_blocking_issues())

        job = service.approve_and_queue_import(migration_request)
        service.run_job(job)
        migration_request.refresh_from_db()

        self.assertEqual(migration_request.status, LegacyMigrationStatus.COMPLETED)
        self.assertEqual(ResourceFile.objects.count(), 1)
        shared_file = ResourceFile.objects.get()
        contents = Content.objects.filter(playlist__name="Duplicate Files Shelf")
        self.assertEqual(contents.count(), 2)
        for content in contents:
            self.assertEqual(content.resource_file, shared_file)
        # The duplicate copy was cleaned up after the reuse was detected.
        self.assertFalse(
            (Path(settings.MEDIA_ROOT) / "Duplicate Beta" / "english.mp4").exists()
        )

    def test_player_url_only_content_requires_view_permission(self):
        owner = UserFactory(instructor=True)
        other_user = UserFactory(instructor=True)
        playlist = PlaylistFactory(owner=owner, published=False)
        content = Content.objects.create(
            playlist=playlist,
            title="URL Only Lecture",
            url="https://example.com/lecture.mp4",
        )

        client = Client()
        client.force_login(
            other_user, backend="django.contrib.auth.backends.ModelBackend"
        )
        response = client.get(reverse("player", args=[content.pk]))
        self.assertEqual(response.status_code, 403)

        client.force_login(owner, backend="django.contrib.auth.backends.ModelBackend")
        response = client.get(reverse("player", args=[content.pk]))
        self.assertEqual(response.status_code, 200)

    def test_build_catalog_client_always_refreshes_snapshot_first(self):
        service = LegacyMigrationService(require_catalog=False)
        call_order = []

        with (
            mock.patch(
                "core.legacy_migration.service.run_legacy_dump",
                side_effect=lambda: call_order.append("dump"),
            ) as dump_mock,
            mock.patch(
                "core.legacy_migration.service.LegacyCatalogClient",
                side_effect=lambda: call_order.append("catalog_client") or mock.Mock(),
            ) as catalog_client_cls,
        ):
            service._build_catalog_client()

        # Unconditional: this must run every time, even if a snapshot from a
        # previous preflight already exists, so preflight never reads stale data.
        dump_mock.assert_called_once()
        catalog_client_cls.assert_called_once()
        self.assertEqual(call_order, ["dump", "catalog_client"])

    def test_build_catalog_client_propagates_dump_failure(self):
        service = LegacyMigrationService(require_catalog=False)

        with (
            mock.patch(
                "core.legacy_migration.service.run_legacy_dump",
                side_effect=RuntimeError("dump exploded"),
            ),
            self.assertRaisesMessage(RuntimeError, "dump exploded"),
        ):
            service._build_catalog_client()

    def test_default_service_construction_refreshes_snapshot_first(self):
        # This is the exact construction path the admin "Run preflight now"
        # action uses (LegacyMigrationService() with no arguments).
        with mock.patch.object(
            LegacyMigrationService, "_build_catalog_client"
        ) as build_mock:
            service = LegacyMigrationService()

        build_mock.assert_called_once()
        self.assertIs(service.catalog_client, build_mock.return_value)


class LegacyDumpTests(TestCase):
    def test_run_legacy_dump_returns_on_success(self):
        with (
            mock.patch.object(
                legacy_dump,
                "build_dump_command",
                return_value=["uv", "run", "scripts/dump_legacy_to_sqlite.py"],
            ),
            mock.patch.object(
                legacy_dump.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="ok", stderr=""
                ),
            ),
        ):
            duration = legacy_dump.run_legacy_dump()
        self.assertGreaterEqual(duration, 0)

    def test_run_legacy_dump_raises_on_nonzero_exit(self):
        with (
            mock.patch.object(
                legacy_dump,
                "build_dump_command",
                return_value=["uv", "run", "scripts/dump_legacy_to_sqlite.py"],
            ),
            mock.patch.object(
                legacy_dump.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(
                    args=[],
                    returncode=1,
                    stdout="",
                    stderr="Legacy dump is already running in another process.",
                ),
            ),
            self.assertRaisesMessage(
                RuntimeError,
                "Legacy dump is already running in another process.",
            ),
        ):
            legacy_dump.run_legacy_dump()

    def test_run_legacy_dump_wraps_missing_script(self):
        with (
            mock.patch.object(
                legacy_dump,
                "build_dump_command",
                side_effect=FileNotFoundError("Legacy dump script was not found: x"),
            ),
            self.assertRaises(RuntimeError),
        ):
            legacy_dump.run_legacy_dump()
