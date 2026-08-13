"""Who may reach the resource intake request page, and who is offered the link.

An intake request commits lab time to digitizing physical media, so only instructors
may make one. The endpoint and the sidebar read the same capability, so nobody is
shown a link that would answer 403 (#379).
"""

from django.test import TestCase
from django.test import modify_settings
from django.test import override_settings
from django.urls import reverse

from ..factories import PlaylistFactory
from ..factories import PlaylistUserAccessFactory
from ..factories import UserFactory
from ..models import PlaylistRole


@modify_settings(
    MIDDLEWARE={"remove": ["mozilla_django_oidc.middleware.SessionRefresh"]}
)
@override_settings(DEBUG=True)
class ResourceIntakeAccessTests(TestCase):
    def setUp(self):
        self.url = reverse("request_resource")
        self.student = UserFactory(student=True, netid="intake_stud")

    def make_ta(self, netid):
        ta = UserFactory(student=True, netid=netid)
        PlaylistUserAccessFactory(
            user=ta, playlist=PlaylistFactory(), playlist_role=PlaylistRole.TA
        )
        return ta

    def test_a_student_is_refused_the_form(self):
        self.client.force_login(self.student)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)

    def test_a_student_is_refused_a_submission(self):
        self.client.force_login(self.student)

        response = self.client.post(self.url, {"resource_title": "Amelie"})

        self.assertEqual(response.status_code, 403)

    def test_a_ta_is_refused_too(self):
        """Editing an instructor's playlist does not carry the capability."""
        self.client.force_login(self.make_ta("intake_ta"))

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)

    def test_an_instructor_reaches_the_form(self):
        self.client.force_login(UserFactory(instructor=True, netid="intake_instr"))

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)

    def test_an_admin_reaches_the_form(self):
        self.client.force_login(UserFactory(admin=True, netid="intake_admin"))

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)

    def test_the_sidebar_hides_the_link_from_students(self):
        self.client.force_login(self.student)

        response = self.client.get(reverse("playlists"))

        self.assertNotContains(response, self.url)

    def test_the_sidebar_hides_the_link_from_tas(self):
        self.client.force_login(self.make_ta("intake_ta2"))

        response = self.client.get(reverse("playlists"))

        self.assertNotContains(response, self.url)

    def test_the_sidebar_offers_the_link_to_an_instructor(self):
        self.client.force_login(UserFactory(instructor=True, netid="intake_instr2"))

        response = self.client.get(reverse("playlists"))

        self.assertContains(response, self.url)
