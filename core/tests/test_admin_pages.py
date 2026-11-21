from django.contrib import admin
from django.db import connection
from django.test import TestCase
from django.test import modify_settings
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core.factories import ContentFactory
from core.factories import MuteAnnotationFactory
from core.factories import SubtitleFactory
from core.models import User


@modify_settings(
    MIDDLEWARE={"remove": ["mozilla_django_oidc.middleware.SessionRefresh"]}
)
@override_settings(DEBUG=True)
class AdminPagesTests(TestCase):
    def setUp(self):
        self.client.force_login(
            User.objects.create_superuser(username="admin-byuid", password="password")
        )
        ContentFactory()

    def admin_url(self, model, view):
        return reverse(f"admin:{model._meta.app_label}_{model._meta.model_name}_{view}")

    def test_every_admin_search_works(self):
        for model, model_admin in admin.site._registry.items():
            if not model_admin.search_fields:
                continue
            with self.subTest(model=model.__name__):
                response = self.client.get(
                    self.admin_url(model, "changelist"), {"q": "x"}
                )
                self.assertEqual(response.status_code, 200)

    def add_page_query_counts(self):
        counts = {}
        for model in admin.site._registry:
            if model._meta.app_label not in ("core", "ic"):
                continue
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get(self.admin_url(model, "add"))
            self.assertIn(response.status_code, (200, 403), model.__name__)
            counts[model.__name__] = len(queries)
        return counts

    def autocomplete_query_counts(self):
        counts = {}
        for model, model_admin in admin.site._registry.items():
            for field_name in model_admin.autocomplete_fields:
                with CaptureQueriesContext(connection) as queries:
                    response = self.client.get(
                        reverse("admin:autocomplete"),
                        {
                            "app_label": model._meta.app_label,
                            "model_name": model._meta.model_name,
                            "field_name": field_name,
                        },
                    )
                self.assertEqual(response.status_code, 200)
                counts[f"{model.__name__}.{field_name}"] = len(queries)
        return counts

    def query_counts(self):
        return self.add_page_query_counts() | self.autocomplete_query_counts()

    def add_related_rows(self):
        for row_factory in (ContentFactory, MuteAnnotationFactory, SubtitleFactory):
            row_factory()

    def test_admin_forms_do_not_slow_down_as_tables_grow(self):
        self.add_related_rows()
        self.query_counts()  # warm per-process caches such as content types
        before = self.query_counts()
        for _ in range(3):
            self.add_related_rows()

        self.assertEqual(self.query_counts(), before)
