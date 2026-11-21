from django.contrib import admin
from django.test import TestCase
from django.test import modify_settings
from django.test import override_settings
from django.urls import reverse

from core.factories import ContentFactory
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

    def test_every_admin_add_page_loads(self):
        for model, model_admin in admin.site._registry.items():
            if model._meta.app_label not in ("core", "ic"):
                continue
            with self.subTest(model=model.__name__):
                response = self.client.get(self.admin_url(model, "add"))
                self.assertIn(response.status_code, (200, 403))
