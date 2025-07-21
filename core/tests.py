# Create your tests here.

from django.test import TestCase
from . import api


class ApiTests(TestCase):
    def test_build_auth_header(self):
        new_api = api.Api()
        re = r"Bearer[ ]\S*"
        result = new_api.build_auth_header()
        self.assertRegex(result, re)

    def test_get_current_year_term(self):
        re = r"[0-9]{4}[1-6]"
        new_api = api.Api()
        result = new_api.get_current_year_term()
        self.assertRegex(result, re)
