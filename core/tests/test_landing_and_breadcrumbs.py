from django.test import TestCase
from django.test import modify_settings
from django.test import override_settings
from django.urls import reverse

from ..factories import PlaylistFactory
from ..factories import UserFactory


@modify_settings(
    MIDDLEWARE={"remove": ["mozilla_django_oidc.middleware.SessionRefresh"]}
)
@override_settings(DEBUG=True)
class LandingPageTests(TestCase):
    def setUp(self):
        self.instructor = UserFactory(instructor=True, netid="instr1")
        self.student = UserFactory(student=True, netid="stud1")

    def test_root_redirects_to_playlists(self):
        self.client.force_login(self.student)

        response = self.client.get("/")

        self.assertRedirects(response, reverse("playlists"))

    def test_root_redirects_instructors_too(self):
        self.client.force_login(self.instructor)

        response = self.client.get("/")

        self.assertRedirects(response, reverse("playlists"))

    def test_about_page_no_longer_offers_a_sign_in_link(self):
        """The whole site is behind LoginRequiredMiddleware, so the button could only
        ever be seen by someone already signed in."""
        self.client.force_login(self.student)

        response = self.client.get(reverse("about"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, reverse("oidc_authentication_init"))

    def test_whats_new_page_renders(self):
        """Asserts on structure, not wording: the copy on this page is expected to be
        rewritten as the legacy differences shift."""
        self.client.force_login(self.student)

        response = self.client.get(reverse("whats_new"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "terminology-table")


@modify_settings(
    MIDDLEWARE={"remove": ["mozilla_django_oidc.middleware.SessionRefresh"]}
)
@override_settings(DEBUG=True)
class WhatsNewBannerTests(TestCase):
    def setUp(self):
        self.instructor = UserFactory(instructor=True, netid="instr2")
        self.student = UserFactory(student=True, netid="stud2")
        self.lab_assistant = UserFactory(lab_assistant=True, netid="labasst1")

    def _get_playlists(self, user):
        self.client.force_login(user)
        return self.client.get(reverse("playlists"))

    def test_instructors_see_the_banner(self):
        self.assertContains(self._get_playlists(self.instructor), "whats-new-banner")

    def test_lab_assistants_see_the_banner(self):
        self.assertContains(self._get_playlists(self.lab_assistant), "whats-new-banner")

    def test_students_do_not_see_the_banner(self):
        self.assertNotContains(self._get_playlists(self.student), "whats-new-banner")

    def test_students_get_no_create_playlist_form(self):
        """playlists.js dereferences #new-playlist-form, so its absence for students is
        the precondition behind that module's null guard."""
        self.assertNotContains(self._get_playlists(self.student), "new-playlist-form")


@modify_settings(
    MIDDLEWARE={"remove": ["mozilla_django_oidc.middleware.SessionRefresh"]}
)
@override_settings(DEBUG=True)
class LegacyMigrationLinkTests(TestCase):
    """A link is only offered where the endpoint behind it would actually answer.

    Migration disabled answers 404, and a user without the capability answers 403,
    so both halves of the gate have to reach the template (#379).
    """

    def setUp(self):
        self.instructor = UserFactory(instructor=True, netid="instr4")
        self.student = UserFactory(student=True, netid="stud4")
        self.link = reverse("legacy_migration_requests")

    @override_settings(DEBUG=True, LEGACY_MIGRATION_ENABLED=False)
    def test_the_banner_omits_the_link_when_migration_is_disabled(self):
        self.client.force_login(self.instructor)

        response = self.client.get(reverse("playlists"))

        self.assertContains(response, "whats-new-banner")
        self.assertNotContains(response, self.link)

    @override_settings(DEBUG=True, LEGACY_MIGRATION_ENABLED=True)
    def test_the_banner_offers_the_link_to_an_instructor_when_enabled(self):
        self.client.force_login(self.instructor)

        response = self.client.get(reverse("playlists"))

        self.assertContains(response, self.link)

    @override_settings(DEBUG=True, LEGACY_MIGRATION_ENABLED=True)
    def test_whats_new_hides_the_migration_section_from_students(self):
        self.client.force_login(self.student)

        response = self.client.get(reverse("whats_new"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.link)

    @override_settings(DEBUG=True, LEGACY_MIGRATION_ENABLED=True)
    def test_whats_new_shows_the_migration_section_to_instructors(self):
        self.client.force_login(self.instructor)

        response = self.client.get(reverse("whats_new"))

        self.assertContains(response, self.link)

    @override_settings(DEBUG=True, LEGACY_MIGRATION_ENABLED=True)
    def test_the_sidebar_hides_the_link_from_students(self):
        self.client.force_login(self.student)

        response = self.client.get(reverse("playlists"))

        self.assertNotContains(response, self.link)


@modify_settings(
    MIDDLEWARE={"remove": ["mozilla_django_oidc.middleware.SessionRefresh"]}
)
@override_settings(DEBUG=True)
class BreadcrumbTests(TestCase):
    def setUp(self):
        self.instructor = UserFactory(instructor=True, netid="instr3")
        self.playlist = PlaylistFactory(owner=self.instructor, name="Phonetics 101")

    def test_playlists_page_shows_no_trail(self):
        """A lone root crumb would only restate the page's own heading."""
        self.client.force_login(self.instructor)

        response = self.client.get(reverse("playlists"))

        self.assertNotContains(response, 'aria-label="Breadcrumb"')

    def test_playlist_info_trails_back_to_playlists(self):
        self.client.force_login(self.instructor)

        response = self.client.get(
            reverse("playlist_info", args=[self.playlist.pk]),
        )

        self.assertContains(response, 'aria-label="Breadcrumb"')
        self.assertContains(response, f'href="{reverse("playlists")}"')
        self.assertContains(response, 'aria-current="page"')
        self.assertContains(response, "Phonetics 101")

    def test_whats_new_trails_back_to_playlists(self):
        self.client.force_login(self.instructor)

        response = self.client.get(reverse("whats_new"))

        self.assertContains(response, 'aria-label="Breadcrumb"')
        self.assertContains(response, f'href="{reverse("playlists")}"')
