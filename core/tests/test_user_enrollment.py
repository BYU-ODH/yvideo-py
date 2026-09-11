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


class EnrollmentSyncTestCase(TestCase):
    """Drives update_user_enrollment against a fake API pinned to 20265/20271."""

    def setUp(self):
        self.user = UserFactory(username="123456789", netid="tstudent")
        self.enrollment_calls = []

    def run_update(self, is_two_weeks_from_end, current, upcoming=None):
        def enrollments(net_id, yearterm):
            self.enrollment_calls.append((net_id, yearterm))
            return {"20265": current, "20271": upcoming}[yearterm]

        with patch("core.model_utils.Api") as mock_api:
            api = mock_api.return_value
            api.get_current_year_term.return_value = {
                "yearterm": "20265",
                "is_two_weeks_from_end": is_two_weeks_from_end,
            }
            api.calculate_next_year_term.return_value = "20271"
            api.get_student_enrollments.side_effect = enrollments
            return update_user_enrollment(self.user)


class UpdateUserEnrollmentTests(EnrollmentSyncTestCase):
    def test_enrollments_are_looked_up_by_netid_not_byu_id(self):
        self.run_update(True, [enrollment_record()], [])

        self.assertEqual(
            self.enrollment_calls, [("tstudent", "20265"), ("tstudent", "20271")]
        )

    def test_a_user_without_a_netid_is_not_looked_up(self):
        self.user.netid = ""
        self.user.save()

        result = self.run_update(False, [enrollment_record()])

        self.assertEqual(result["result_message"], "Unknown user")
        self.assertEqual(self.enrollment_calls, [])
        self.assertEqual(UserCourses.objects.count(), 0)

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


class EnrollmentRevocationTests(EnrollmentSyncTestCase):
    """Course-derived access follows current enrollment, so drops have to propagate."""

    def test_dropping_one_course_revokes_only_that_course(self):
        self.run_update(
            False,
            [
                enrollment_record(catalog_number="101"),
                enrollment_record(catalog_number="102"),
            ],
        )

        self.run_update(False, [enrollment_record(catalog_number="101")])

        remaining = UserCourses.objects.filter(user=self.user)
        self.assertEqual(
            [association.course.catalog_number for association in remaining], ["101"]
        )

    def test_dropping_every_course_revokes_everything(self):
        self.run_update(False, [enrollment_record()])

        result = self.run_update(False, [])

        self.assertTrue(result["is_current_sem_updated"])
        self.assertEqual(UserCourses.objects.count(), 0)

    def test_a_withdrawn_record_does_not_grant_access(self):
        withdrawn = enrollment_record()
        withdrawn["withdraw_flag"] = "Y"

        self.run_update(False, [withdrawn])

        self.assertEqual(UserCourses.objects.count(), 0)

    def test_withdrawing_from_a_course_revokes_it(self):
        self.run_update(False, [enrollment_record()])
        withdrawn = enrollment_record()
        withdrawn["withdraw_flag"] = "Y"

        self.run_update(False, [withdrawn])

        self.assertEqual(UserCourses.objects.count(), 0)

    def test_a_failed_lookup_leaves_existing_access_alone(self):
        self.run_update(False, [enrollment_record()])

        result = self.run_update(False, None)

        self.assertFalse(result["is_current_sem_updated"])
        self.assertEqual(UserCourses.objects.count(), 1)

    def test_another_terms_associations_are_untouched(self):
        self.run_update(True, [enrollment_record()], [enrollment_record()])

        self.run_update(False, [])

        self.assertEqual(UserCourses.objects.filter(yearterm="20265").count(), 0)
        self.assertEqual(UserCourses.objects.filter(yearterm="20271").count(), 1)

    def test_another_users_associations_are_untouched(self):
        classmate = UserFactory(username="987654321", netid="cstudent")
        self.run_update(False, [enrollment_record()])
        course = Course.objects.get()
        UserCourses.objects.create(
            user=classmate, course=course, yearterm=course.yearterm
        )

        self.run_update(False, [])

        self.assertEqual(UserCourses.objects.filter(user=self.user).count(), 0)
        self.assertEqual(UserCourses.objects.filter(user=classmate).count(), 1)
