from unittest.mock import patch

from django.test import TestCase

from core.factories import UserFactory
from core.model_utils import get_or_create_course
from core.model_utils import update_user_enrollment
from core.models import Course
from core.models import UserCourses


def enrollment_record(catalog_number="101", section_number="001"):
    return {
        "curriculum_id": "01234",
        "title_code": "001",
        "section_number": section_number,
        "teaching_area": "SPAN",
        "catalog_number": catalog_number,
        "catalog_suffix": "",
        "credit_hours": "3.0",
        "withdraw_flag": "N",
        "audit_flag": "N",
    }


class GetOrCreateCourseTests(TestCase):
    def test_creates_course_with_yearterm(self):
        course = get_or_create_course(enrollment_record(), "20265")

        self.assertIsInstance(course, Course)
        self.assertEqual(course.dept, "SPAN")
        self.assertEqual(course.catalog_number, "101")
        self.assertEqual(course.section_number, "001")
        self.assertEqual(course.yearterm, "20265")

    def test_reuses_existing_course(self):
        first = get_or_create_course(enrollment_record(), "20265")
        second = get_or_create_course(enrollment_record(), "20265")

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Course.objects.count(), 1)

    def test_same_course_in_a_different_term_is_a_separate_row(self):
        get_or_create_course(enrollment_record(), "20265")
        get_or_create_course(enrollment_record(), "20271")

        self.assertEqual(Course.objects.count(), 2)


class UpdateUserEnrollmentTests(TestCase):
    def setUp(self):
        self.user = UserFactory(username="123456789")

    def run_update(self, is_two_weeks_from_end, current, upcoming=None):
        def enrollments(net_id, yearterm):
            return {"20265": current, "20271": upcoming}.get(yearterm)

        with patch("core.model_utils.Api") as mock_api:
            api = mock_api.return_value
            api.get_current_year_term.return_value = {
                "yearterm": "20265",
                "is_two_weeks_from_end": is_two_weeks_from_end,
            }
            api.calculate_next_year_term.return_value = "20271"
            api.get_student_enrollments.side_effect = enrollments
            return update_user_enrollment(self.user)

    def test_current_term_enrollments_are_saved(self):
        result = self.run_update(False, [enrollment_record()])

        self.assertTrue(result["is_current_sem_updated"])
        association = UserCourses.objects.get(user=self.user)
        self.assertEqual(association.yearterm, "20265")
        self.assertEqual(association.course.yearterm, "20265")

    def test_next_term_enrollments_are_saved_with_the_next_yearterm(self):
        result = self.run_update(
            True,
            [enrollment_record(section_number="001")],
            [enrollment_record(section_number="002")],
        )

        self.assertTrue(result["is_next_sem_updated"])
        next_term = UserCourses.objects.get(yearterm="20271")
        self.assertEqual(next_term.course.section_number, "002")
        self.assertEqual(next_term.course.yearterm, "20271")

    def test_enrollment_and_course_yearterms_always_agree(self):
        self.run_update(
            True,
            [enrollment_record(section_number="001")],
            [enrollment_record(section_number="002")],
        )

        for association in UserCourses.objects.all():
            self.assertEqual(association.yearterm, association.course.yearterm)

    def test_repeated_logins_do_not_duplicate_associations(self):
        self.run_update(False, [enrollment_record()])
        self.run_update(False, [enrollment_record()])

        self.assertEqual(UserCourses.objects.count(), 1)
