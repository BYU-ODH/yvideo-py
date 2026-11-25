import re

from django.test import TestCase
from django.test import modify_settings
from django.urls import reverse

from ..factories import UserFactory
from ..user_guide import PAGES

NEXT_LINK = re.compile(r'class="guide-pager-next" href="([^"]+)"')


@modify_settings(
    MIDDLEWARE={"remove": ["mozilla_django_oidc.middleware.SessionRefresh"]}
)
class UserGuideTests(TestCase):
    def setUp(self):
        self.client.force_login(UserFactory(student=True))

    def test_site_menu_links_to_the_guide(self):
        response = self.client.get(reverse("playlists"))

        self.assertContains(response, f'href="{reverse("user_guide")}"')

    def test_following_next_from_the_introduction_reads_every_page_in_order(self):
        visited = []
        url = reverse("user_guide")
        while url:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
            visited.append(url)
            match = NEXT_LINK.search(response.content.decode())
            url = match and match.group(1)

        self.assertEqual(visited, [page.url for page in PAGES])

    def test_unknown_page_is_not_found(self):
        response = self.client.get("/user-guide/no-such-page/")

        self.assertEqual(response.status_code, 404)
