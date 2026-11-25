import time

from django.test import TestCase
from django.urls import reverse

from core.factories import CourseFactory
from core.factories import PlaylistFactory
from core.factories import PlaylistUserAccessFactory
from core.factories import UserFactory
from core.models import Course
from core.models import PlaylistRole


class PlaylistCourseEndpointTests(TestCase):
    def setUp(self):
        self.playlist = PlaylistFactory()
        self.login(self.playlist.owner)
        self.course_data = {
            "dept": "SPAN",
            "catalog_number": "101",
            "yearterm": "20265",
        }

    def login(self, user):
        self.client.force_login(user)
        session = self.client.session
        session["oidc_id_token_expiration"] = time.time() + 3600
        session.save()

    def test_assign_course(self):
        response = self.client.post(
            reverse("assign_playlist_to_course", args=[self.playlist.pk]),
            {**self.course_data, "sections": "001"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"success": True})
        course = self.playlist.courses.get()
        self.assertEqual(
            (
                course.dept,
                course.catalog_number,
                course.section_number,
                course.yearterm,
            ),
            ("SPAN", "101", "001", "20265"),
        )

    def test_assign_multiple_course_sections(self):
        response = self.client.post(
            reverse("assign_playlist_to_course", args=[self.playlist.pk]),
            {**self.course_data, "sections": "001, 002, 003"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"success": True})
        self.assertCountEqual(
            self.playlist.courses.values_list("section_number", flat=True),
            ["001", "002", "003"],
        )

    def test_read_assigned_course(self):
        course = CourseFactory(**self.course_data, section_number="001")
        self.playlist.courses.add(course)

        response = self.client.get(
            reverse("edit_playlist_courses_form", args=[self.playlist.pk]),
            self.course_data,
        )

        self.assertContains(response, 'value="001"')
        self.assertEqual(self.playlist.courses.get(), course)

    def test_edit_assigned_course_sections(self):
        course = CourseFactory(**self.course_data, section_number="001")
        self.playlist.courses.add(course)

        response = self.client.post(
            reverse("update_playlist_course_sections", args=[self.playlist.pk]),
            {
                **self.course_data,
                "original_dept": "SPAN",
                "original_catalog_number": "101",
                "sections": "002",
            },
        )

        self.assertContains(response, 'value="002"')
        self.assertEqual(
            list(self.playlist.courses.values_list("section_number", flat=True)),
            ["002"],
        )

    def test_edit_adds_and_removes_course_sections(self):
        courses = [
            CourseFactory(**self.course_data, section_number=section)
            for section in ("001", "002", "003")
        ]
        self.playlist.courses.add(*courses)

        response = self.client.post(
            reverse("update_playlist_course_sections", args=[self.playlist.pk]),
            {
                **self.course_data,
                "original_dept": "SPAN",
                "original_catalog_number": "101",
                "sections": "002, 004, 005",
            },
        )

        self.assertContains(response, 'value="002, 004, 005"')
        self.assertCountEqual(
            self.playlist.courses.values_list("section_number", flat=True),
            ["002", "004", "005"],
        )

    def test_edit_changes_department_and_catalog_number_with_same_sections(self):
        courses = [
            CourseFactory(**self.course_data, section_number=section)
            for section in ("001", "002", "003")
        ]
        self.playlist.courses.add(*courses)

        response = self.client.post(
            reverse("update_playlist_course_sections", args=[self.playlist.pk]),
            {
                "dept": "FREN",
                "catalog_number": "201",
                "yearterm": "20265",
                "original_dept": "SPAN",
                "original_catalog_number": "101",
                "sections": "001, 002, 003",
            },
        )

        self.assertContains(response, 'value="FREN"')
        self.assertContains(response, 'value="201"')
        self.assertContains(response, 'value="001, 002, 003"')
        self.assertFalse(self.playlist.courses.filter(**self.course_data).exists())
        self.assertCountEqual(
            self.playlist.courses.values_list(
                "dept", "catalog_number", "section_number", "yearterm"
            ),
            [
                ("FREN", "201", "001", "20265"),
                ("FREN", "201", "002", "20265"),
                ("FREN", "201", "003", "20265"),
            ],
        )

    def test_remove_course_assignment(self):
        course = CourseFactory(**self.course_data, section_number="001")
        self.playlist.courses.add(course)

        response = self.client.post(
            reverse("unassign_playlist_from_course", args=[self.playlist.pk]),
            self.course_data,
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")
        self.assertFalse(self.playlist.courses.exists())
        self.assertTrue(Course.objects.filter(pk=course.pk).exists())

    def test_collaborator_can_edit_and_unassign_courses(self):
        collaborator = UserFactory()
        PlaylistUserAccessFactory(
            playlist=self.playlist,
            user=collaborator,
            playlist_role=PlaylistRole.COLLABORATOR,
        )
        self.login(collaborator)
        course = CourseFactory(**self.course_data, section_number="001")
        self.playlist.courses.add(course)

        response = self.client.post(
            reverse("update_playlist_course_sections", args=[self.playlist.pk]),
            {
                **self.course_data,
                "original_dept": "SPAN",
                "original_catalog_number": "101",
                "sections": "002",
            },
        )

        self.assertContains(response, 'value="002"')
        self.assertEqual(
            list(self.playlist.courses.values_list("section_number", flat=True)),
            ["002"],
        )
        response = self.client.post(
            reverse("unassign_playlist_from_course", args=[self.playlist.pk]),
            self.course_data,
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 204)
        self.assertFalse(self.playlist.courses.exists())

    def test_student_and_outsider_cannot_manage_course_assignments(self):
        student = UserFactory(student=True)
        outsider = UserFactory(student=True)
        PlaylistUserAccessFactory(
            playlist=self.playlist, user=student, playlist_role=PlaylistRole.STUDENT
        )
        course = CourseFactory(**self.course_data, section_number="001")
        self.playlist.courses.add(course)
        before = list(Course.objects.values())
        for user in (student, outsider):
            self.login(user)
            for view_name in (
                "edit_playlist_courses_form",
                "assign_playlist_to_course",
                "update_playlist_course_sections",
                "unassign_playlist_from_course",
            ):
                with self.subTest(user=user, view_name=view_name):
                    url = reverse(view_name, args=[self.playlist.pk])
                    if view_name == "edit_playlist_courses_form":
                        response = self.client.get(url, self.course_data)
                    elif view_name == "unassign_playlist_from_course":
                        response = self.client.post(
                            url, self.course_data, content_type="application/json"
                        )
                    else:
                        response = self.client.post(
                            url,
                            {
                                **self.course_data,
                                "sections": "002",
                                "original_dept": "SPAN",
                                "original_catalog_number": "101",
                            },
                        )

                    self.assertEqual(response.status_code, 403)
                    self.assertEqual(list(self.playlist.courses.all()), [course])
                    self.assertEqual(list(Course.objects.values()), before)
