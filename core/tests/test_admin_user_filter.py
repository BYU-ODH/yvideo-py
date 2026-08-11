from django.contrib import admin
from django.contrib.auth.models import Group
from django.test import TestCase
from django.test import modify_settings
from django.test import override_settings
from django.urls import reverse

from core.factories import UserFactory
from core.models import LAB_ASSISTANT_GROUP_NAME
from core.models import User


@modify_settings(
    MIDDLEWARE={"remove": ["mozilla_django_oidc.middleware.SessionRefresh"]}
)
@override_settings(DEBUG=True)
class UserAdminGroupFilterTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username="admin-byuid", password="password"
        )
        self.client.force_login(self.admin_user)
        self.changelist_url = reverse("admin:core_user_changelist")
        self.lab_assistant = UserFactory(lab_assistant=True, netid="labasst")
        self.other_user = UserFactory(netid="student")

    def filtered_users(self, **params):
        response = self.client.get(self.changelist_url, params)
        self.assertEqual(response.status_code, 200)
        return list(response.context["cl"].result_list)

    def test_group_filter_is_available(self):
        response = self.client.get(self.changelist_url)

        filter_titles = [spec.title for spec in response.context["cl"].filter_specs]
        self.assertIn("groups", filter_titles)

    def test_filtering_by_lab_assistant_group_returns_only_lab_assistants(self):
        group = Group.objects.get(name=LAB_ASSISTANT_GROUP_NAME)

        users = self.filtered_users(groups__id__exact=group.pk)

        self.assertEqual(users, [self.lab_assistant])

    def test_unfiltered_changelist_includes_users_without_groups(self):
        users = self.filtered_users()

        self.assertIn(self.other_user, users)
        self.assertIn(self.lab_assistant, users)

    def test_group_names_column_lists_memberships(self):
        user_admin = admin.site._registry[User]

        self.assertEqual(
            user_admin.group_names(self.lab_assistant), LAB_ASSISTANT_GROUP_NAME
        )
        self.assertEqual(user_admin.group_names(self.other_user), "—")
