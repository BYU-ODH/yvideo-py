import shutil
import tempfile

from django.test import Client
from django.test import TestCase
from django.test import override_settings
from django.urls import reverse

from core.dev_features import DEMO_ADMIN_USERNAME
from core.dev_seed import seed_demo_data
from core.models import AnnotationSet
from core.models import BlankAnnotation
from core.models import Collection
from core.models import CollectionRole
from core.models import CollectionUserAccess
from core.models import CommentAnnotation
from core.models import Content
from core.models import MuteAnnotation
from core.models import ResourceAccess
from core.models import ResourceFile
from core.models import ResourceFileKey
from core.models import Track
from core.models import User


class DemoSeedDataTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._media_root = tempfile.mkdtemp()
        cls._settings = override_settings(
            DEBUG=True,
            DEV_QUICK_LOGIN_ENABLED=True,
            MEDIA_ROOT=cls._media_root,
            ALLOWED_HOSTS=["localhost", "127.0.0.1", "example.com", "testserver"],
            SECRET_KEY="test-secret-key",
        )
        cls._settings.enable()

    @classmethod
    def tearDownClass(cls):
        cls._settings.disable()
        shutil.rmtree(cls._media_root, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        seed_demo_data()

    def test_seed_creates_expected_accounts_and_access(self):
        admin_user = User.objects.get(username=DEMO_ADMIN_USERNAME)
        birds_content = Content.objects.get(title="Birds Overview")
        alice = User.objects.get(username="111227777")
        admin_owned_collections = Collection.objects.filter(owner=admin_user)

        self.assertTrue(admin_user.is_superuser)
        self.assertTrue(admin_user.is_staff)
        self.assertEqual(admin_owned_collections.count(), 2)
        self.assertTrue(
            admin_owned_collections.filter(
                name="Local Admin / Demo Review Shelf"
            ).exists()
        )
        self.assertTrue(
            admin_owned_collections.filter(name="Local Admin / Draft Sandbox").exists()
        )
        self.assertEqual(
            CollectionUserAccess.objects.filter(
                user=admin_user,
                collection__owner=admin_user,
                collection_role=CollectionRole.INSTRUCTOR,
            ).count(),
            2,
        )
        self.assertTrue(
            ResourceAccess.objects.filter(
                user=birds_content.collection.owner,
                resource=birds_content.resource_file.resource,
            ).exists()
        )
        self.assertTrue(
            CollectionUserAccess.objects.filter(
                collection=birds_content.collection,
                user=alice,
            ).exists()
        )
        self.assertTrue(alice.can_view_content(birds_content))

    def test_seed_creates_track_based_editor_data_for_fixture_covered_models(self):
        admin_user = User.objects.get(username=DEMO_ADMIN_USERNAME)
        birds_annotation_set = AnnotationSet.objects.get(
            name="Professor Ada Birds Annotations"
        )
        grid_annotation_set = AnnotationSet.objects.get(
            name="Professor Ben Grid Annotations"
        )
        birds_track = Track.objects.get(
            annotation_set=birds_annotation_set,
            name="Track 1",
        )
        grid_track = Track.objects.get(
            annotation_set=grid_annotation_set, name="Track 1"
        )

        self.assertEqual(AnnotationSet.objects.count(), 2)
        self.assertEqual(Track.objects.count(), 2)
        self.assertEqual(MuteAnnotation.objects.filter(active=True).count(), 2)
        self.assertEqual(
            CommentAnnotation.objects.filter(active=True, track=birds_track).count(), 3
        )
        self.assertEqual(
            CommentAnnotation.objects.filter(
                active=True,
                track=birds_track,
            ).count(),
            3,
        )
        self.assertEqual(
            CommentAnnotation.objects.filter(active=True, track=grid_track).count(), 1
        )
        self.assertEqual(
            set(
                BlankAnnotation.objects.filter(active=True).values_list(
                    "type", flat=True
                )
            ),
            {"#", "k"},
        )
        self.assertEqual(
            set(birds_annotation_set.editors.values_list("username", flat=True)),
            {DEMO_ADMIN_USERNAME, "111225555"},
        )
        self.assertFalse(grid_annotation_set.editors.exists())
        self.assertTrue(
            ResourceFile.objects.filter(
                resource__name="Grid Overlay",
                burned_in_subtitles_language__lang_tag="es",
            ).exists()
        )
        self.assertTrue(ResourceFileKey.objects.filter(user=admin_user).exists())
        self.assertGreaterEqual(
            Content.objects.filter(clips__isnull=False).distinct().count(),
            2,
        )
        self.assertTrue(Content.objects.filter(clips__isnull=True).exists())

    def test_seed_is_repeatable(self):
        first_counts = {
            "users": User.objects.count(),
            "contents": Content.objects.count(),
            "resource_files": ResourceFile.objects.count(),
        }

        seed_demo_data()

        second_counts = {
            "users": User.objects.count(),
            "contents": Content.objects.count(),
            "resource_files": ResourceFile.objects.count(),
        }
        self.assertEqual(first_counts, second_counts)

    def test_dev_quick_login_logs_in_seeded_admin(self):
        client = Client(HTTP_HOST="localhost")
        response = client.get(reverse("dev_quick_login"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")
        self.assertEqual(
            client.session["_auth_user_id"],
            str(User.objects.get(username=DEMO_ADMIN_USERNAME).pk),
        )

    def test_dev_quick_login_is_disabled_without_flag(self):
        client = Client(HTTP_HOST="localhost")
        with self.settings(DEV_QUICK_LOGIN_ENABLED=False):
            response = client.get(reverse("dev_quick_login"))
        self.assertEqual(response.status_code, 404)

    def test_dev_quick_login_rejects_non_local_host(self):
        client = Client(HTTP_HOST="example.com")
        response = client.get(reverse("dev_quick_login"))
        self.assertEqual(response.status_code, 404)
