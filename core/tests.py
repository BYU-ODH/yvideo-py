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
        self.assertRegex(result["yearterm"], re)

    def test_calculate_next_year_term(self):
        new_api = api.Api()
        fall_to_winter = new_api.calculate_next_year_term("20255")
        self.assertEqual(fall_to_winter, "20261")
        winter_to_spring = new_api.calculate_next_year_term("20261")
        self.assertEqual(winter_to_spring, "20263")
        spring_to_summer = new_api.calculate_next_year_term("20263")
        self.assertEqual(spring_to_summer, "20264")
        summer_to_fall = new_api.calculate_next_year_term("20264")
        self.assertEqual(summer_to_fall, "20265")
