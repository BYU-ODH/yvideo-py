import io
import json
import os
from pathlib import Path
import shutil
import tempfile
from unittest import mock
import uuid

from django.conf import settings
from django.contrib import messages
from django.contrib.messages import get_messages
from django.core.exceptions import ImproperlyConfigured
from django.db import OperationalError
from django.db import connection
from django.db import connections
from django.test import Client
from django.test import TestCase
from django.test import override_settings
from django.urls import reverse
import xxhash

from .factories import CollectionFactory
from .factories import LanguageFactory
from .factories import ResourceFactory
from .factories import UserFactory
from .legacy_migration import LegacyMigrationJob
from .legacy_migration import LegacyMigrationRequest
from .legacy_migration import LegacyMigrationStatus
from .legacy_migration import LegacyMigrationUserResolutionStatus
from .legacy_migration_services import ChecksumCache
from .legacy_migration_services import LegacyCatalogClient
from .legacy_migration_services import LegacyMigrationService
from .models import BlurAnnotation
from .models import BlurAnnotationPosition
from .models import Collection
from .models import CollectionRole
from .models import CollectionUserAccess
from .models import Content
from .models import Resource
from .models import ResourceAccess
from .models import ResourceFile
from .models import Subtitle


@override_settings(
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
                words TEXT,
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
                words TEXT,
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
        language = LanguageFactory(language="English", lang_tag="en")

        owner = UserFactory(netid="profada", byu_id="123456789", instructor=True)
        ta_user = UserFactory(netid="caseyta", byu_id="987654321", instructor=True)
        current_resource = ResourceFactory(
            name="Legacy Birds", requester_netid=owner.netid
        )
        shared_path = self.write_media_file("legacy/shared-birds.mp4")
        current_resource_file = ResourceFile(
            resource=current_resource,
            version="english",
            full_video=True,
        )
        current_resource_file.file.name = "legacy/shared-birds.mp4"
        current_resource_file.save()
        current_collection = CollectionFactory(
            owner=owner, name="Current Birds Collection"
        )
        CollectionUserAccess.objects.create(
            user=ta_user,
            collection=current_collection,
            collection_role=CollectionRole.TA,
        )
        Content.objects.create(
            collection=current_collection,
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
                "words": "",
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
                "words": "",
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
                "language": "en",
                "content": json.dumps([{"start": 0, "end": 1, "text": "Birds"}]),
                "words": "Birds",
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
        self.assertIn(
            "Current Birds Collection",
            file_decision.candidate_matches[0]["collections"],
        )
        self.assertIn("caseyta", file_decision.candidate_matches[0]["instructors"])
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

    def test_import_collection_migrates_files_url_content_permissions_and_annotations(
        self,
    ):
        self.create_legacy_schema()
        LanguageFactory(language="English", lang_tag="en")

        target_owner = UserFactory(netid="profben", byu_id="111111111", instructor=True)
        ta_user = UserFactory(netid="caseyta", byu_id="222222222", instructor=True)

        legacy_collection_id = str(uuid.uuid4())
        legacy_resource_id = str(uuid.uuid4())
        legacy_owner_id = str(uuid.uuid4())
        legacy_ta_id = str(uuid.uuid4())
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
                "username": "caseyta",
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
                            "position": {
                                "0": [2, 10, 20, 30, 40],
                                "1": [4, 11, 21, 30, 40],
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
                "words": "birds, migration",
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
                "words": "",
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
                "language": "en",
                "content": json.dumps(
                    [{"start": 0.0, "end": 2.0, "text": "Hello world"}]
                ),
                "words": "Hello, world",
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

        imported_collection = Collection.objects.get(name="Imported Legacy Shelf")
        imported_contents = Content.objects.filter(
            collection=imported_collection
        ).order_by("title")
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
        self.assertTrue(imported_collection.courses.filter(dept="FILM").exists())

        imported_file_path = Path(imported_video.resource_file.file.path)
        self.assertEqual(
            os.stat(source_path).st_ino, os.stat(imported_file_path).st_ino
        )

        self.assertEqual(imported_video.clips.count(), 1)
        self.assertEqual(imported_video.annotation_set.tracks.count(), 2)
        self.assertEqual(
            BlurAnnotation.objects.filter(
                track__annotation_set=imported_video.annotation_set
            ).count(),
            1,
        )
        self.assertEqual(
            BlurAnnotationPosition.objects.filter(
                blur_annotation__track__annotation_set=imported_video.annotation_set
            ).count(),
            2,
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
                user=ta_user, resource=imported_video.resource
            ).exists()
        )
        self.assertTrue(
            CollectionUserAccess.objects.filter(
                user=target_owner,
                collection=imported_collection,
                collection_role=CollectionRole.INSTRUCTOR,
            ).exists()
        )
        self.assertTrue(
            CollectionUserAccess.objects.filter(
                user=ta_user,
                collection=imported_collection,
                collection_role=CollectionRole.TA,
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

    def test_instructor_request_view_creates_queued_preflight_job(self):
        instructor = UserFactory(instructor=True)
        client = Client()
        client.force_login(instructor)

        response = client.post(
            reverse("create_legacy_migration_request"),
            data={
                "migration_kind": "resource",
                "legacy_reference": str(uuid.uuid4()),
                "request_notes": "Please migrate this resource.",
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

    @override_settings(LEGACY_MIGRATION_ENABLED=False)
    def test_legacy_migration_views_return_404_when_feature_disabled(self):
        instructor = UserFactory(instructor=True)
        client = Client()
        client.force_login(instructor)

        response = client.get(reverse("legacy_migration_requests"))

        self.assertEqual(response.status_code, 404)

    @override_settings(LEGACY_MIGRATION_ENABLED=False)
    def test_manage_collections_hides_legacy_migration_link_when_feature_disabled(self):
        instructor = UserFactory(instructor=True)
        client = Client()
        client.force_login(instructor)

        response = client.get(reverse("manage_collections"))

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
        client.force_login(admin_user)

        with (
            mock.patch("core.admin.LegacyMigrationService") as service_class,
            mock.patch("core.admin.logger") as logger_mock,
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
        client.force_login(admin_user)

        with (
            mock.patch("core.admin.LegacyMigrationService") as service_class,
            mock.patch("core.admin.logger"),
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

    @override_settings(LEGACY_MIGRATION_CREATE_MISSING_USERS=True)
    def test_upsert_user_resolution_handles_missing_autocreate_user(self):
        owner = UserFactory(netid="profada", byu_id="123456789", instructor=True)
        migration_request = LegacyMigrationRequest.objects.create(
            requested_by=owner,
            target_owner=owner,
            migration_kind="collection",
            legacy_reference=str(uuid.uuid4()),
        )
        service = self.build_service()

        with mock.patch(
            "core.legacy_migration_services.create_or_update_user",
            return_value={
                "is_new_user_created": False,
                "user": None,
                "enrollment_update_message": "Course enrollment was not updated",
            },
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
        owner = UserFactory(netid="profada", byu_id="123456789", instructor=True)
        created_user = UserFactory(
            netid="rjr45",
            byu_id="555555555",
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

        with mock.patch(
            "core.legacy_migration_services.create_or_update_user",
            return_value={
                "is_new_user_created": False,
                "user": created_user.to_dict(),
                "enrollment_update_message": "",
            },
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

        with mock.patch("core.legacy_migration_services.subprocess.run") as run_mock:
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
            file_info["absolute_path"],
            "yvideo:/opt/media/y-video/legacy/shared-birds.mp4",
        )
        self.assertEqual(
            file_info["realpath"],
            "yvideo:/opt/media/y-video/legacy/shared-birds.mp4",
        )
        self.assertEqual(file_info["size_bytes"], 12)
        self.assertIsNone(file_info["device"])
        self.assertIsNone(file_info["inode"])
        self.assertEqual(file_info["extension"], ".mp4")
        self.assertEqual(
            run_mock.call_args.args[0][:3],
            ["ssh", "-oBatchMode=yes", "yvideo"],
        )

    def test_remote_legacy_checksum_streams_over_ssh(self):
        checksum_cache = ChecksumCache()
        process = mock.MagicMock()
        process.stdout = io.BytesIO(b"legacy-video")
        process.stderr = io.BytesIO(b"")
        process.wait.return_value = 0
        process.__enter__.return_value = process

        with mock.patch(
            "core.legacy_migration_services.subprocess.Popen",
            return_value=process,
        ) as popen_mock:
            checksum = checksum_cache.get_or_compute_legacy_checksum(
                {
                    "absolute_path": "yvideo:/opt/media/y-video/legacy/shared-birds.mp4",
                    "size_bytes": 12,
                    "mtime_ns": 1700000000000000000,
                }
            )

        self.assertEqual(checksum, xxhash.xxh64(b"legacy-video").hexdigest())
        self.assertEqual(
            popen_mock.call_args.args[0][:3],
            ["ssh", "-oBatchMode=yes", "yvideo"],
        )

    def test_import_file_to_storage_uses_scp_for_remote_source(self):
        service = self.build_service()
        resource = ResourceFactory(name="Imported Lecture")
        source_path = "yvideo:/opt/media/y-video/legacy/imported.mp4"

        with mock.patch("core.legacy_migration_services.subprocess.run") as run_mock:
            relative_name = service._import_file_to_storage(
                source_path,
                resource,
                "english",
            )

        destination = Path(settings.MEDIA_ROOT) / relative_name
        self.assertEqual(relative_name, "Imported Lecture/english.mp4")
        self.assertTrue(destination.parent.exists())
        run_mock.assert_called_once_with(
            ["scp", "-p", "-oBatchMode=yes", source_path, str(destination)],
            capture_output=True,
            text=True,
            check=True,
        )
