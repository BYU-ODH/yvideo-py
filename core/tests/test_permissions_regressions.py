"""The 21 confirmed exploits from issue #111, inverted.

Each of these was executed against the tree before this work as a freshly created,
unrelated PrivilegeLevel.STUDENT account with no relationship to the target object, and
succeeded. They assert 403 and no state change now. One test per row of the audit table,
so a regression names the endpoint that regressed.

`login` rather than force_login: mozilla_django_oidc's SessionRefresh intercepts
non-AJAX GETs whose session carries no oidc_id_token_expiration and 302s them, which
would mask a 403 as a redirect on every GET-based probe.
"""

import json
import time

from django.test import TestCase
from django.urls import reverse

from core.factories import AnnotationSetFactory
from core.factories import ContentFactory
from core.factories import CourseFactory
from core.factories import PlaylistFactory
from core.factories import PlaylistUserAccessFactory
from core.factories import ResourceFactory
from core.factories import ResourceFileFactory
from core.factories import ResourceFileKeyFactory
from core.factories import SubtitleFactory
from core.factories import TrackFactory
from core.factories import UserCourseFactory
from core.factories import UserFactory
from core.models import AnnotationSet
from core.models import Content
from core.models import PlaylistRole
from core.models import Track


def login(client, user):
    client.force_login(user)
    session = client.session
    session["oidc_id_token_expiration"] = time.time() + 3600
    session.save()


class StrangerIsRefusedTests(TestCase):
    """A student with no relationship at all to the target objects."""

    def setUp(self):
        self.owner = UserFactory(instructor=True)
        self.playlist = PlaylistFactory(owner=self.owner, published=True)
        self.resource_file = ResourceFileFactory()
        self.resource = self.resource_file.resource
        self.content = ContentFactory(
            playlist=self.playlist, resource_file=self.resource_file, published=True
        )
        self.annotation_set = AnnotationSetFactory(
            owner=self.owner, resource=self.resource
        )
        self.track = TrackFactory(annotation_set=self.annotation_set)
        self.content.annotation_set = self.annotation_set
        self.content.save()
        self.subtitle = SubtitleFactory(resource=self.resource, owner=self.owner)

        self.stranger = UserFactory(student=True)
        login(self.client, self.stranger)

    def assert_refused(self, response):
        self.assertEqual(response.status_code, 403, response.content[:200])

    def post_json(self, url, payload=None):
        return self.client.post(
            url, data=json.dumps(payload or {}), content_type="application/json"
        )

    # --- streaming and playback -------------------------------------------------

    def test_stream_file_refuses_another_users_key(self):
        key = ResourceFileKeyFactory(user=self.owner, resource_file=self.resource_file)

        self.assert_refused(self.client.get(reverse("stream_file", args=[key.pk])))

    def test_get_player_data_refuses_a_stranger(self):
        self.assert_refused(
            self.client.post(reverse("get_player_data", args=[self.content.pk]))
        )

    def test_player_refuses_a_stranger(self):
        self.assert_refused(self.client.get(reverse("player", args=[self.content.pk])))

    # --- playlists --------------------------------------------------------------

    def test_playlist_info_refuses_a_stranger(self):
        self.assert_refused(
            self.client.get(reverse("playlist_info", args=[self.playlist.pk]))
        )

    def test_display_playlist_settings_refuses_a_stranger(self):
        self.assert_refused(
            self.client.get(
                reverse("display_playlist_settings", args=[self.playlist.pk])
            )
        )

    def test_render_course_assignment_refuses_a_stranger(self):
        self.assert_refused(
            self.post_json(
                reverse("render_course_assignment", args=[self.playlist.pk]),
                {"semester": "5", "year": "2026"},
            )
        )

    def test_assign_playlist_to_course_refuses_a_stranger(self):
        response = self.post_json(
            reverse("assign_playlist_to_course", args=[self.playlist.pk]),
            {
                "dept": "SPAN",
                "catalog_number": "101",
                "sections": ["001"],
                "year": "2026",
                "semester": "5",
            },
        )

        self.assert_refused(response)
        self.assertFalse(self.playlist.courses.exists())

    def test_update_playlist_course_sections_refuses_a_stranger(self):
        course = CourseFactory(dept="SPAN", catalog_number="101", section_number="001")
        self.playlist.courses.add(course)

        response = self.post_json(
            reverse("update_playlist_course_sections", args=[self.playlist.pk]),
            {
                "dept": "SPAN",
                "catalog_number": "101",
                "sections": ["002"],
                "year": course.yearterm[:4],
                "semester": course.yearterm[4:],
            },
        )

        self.assert_refused(response)
        self.assertEqual(list(self.playlist.courses.all()), [course])

    def test_unassign_playlist_from_course_refuses_a_stranger(self):
        course = CourseFactory(dept="SPAN", catalog_number="101", section_number="001")
        self.playlist.courses.add(course)

        response = self.post_json(
            reverse("unassign_playlist_from_course", args=[self.playlist.pk]),
            {
                "dept": "SPAN",
                "catalog_number": "101",
                "year": course.yearterm[:4],
                "semester": course.yearterm[4:],
            },
        )

        self.assert_refused(response)
        self.assertEqual(list(self.playlist.courses.all()), [course])

    def test_update_playlist_settings_refuses_a_stranger(self):
        response = self.client.post(
            reverse("update_playlist_settings", args=[self.playlist.pk]),
            data={"name": "Stolen", "published": "on", "archived": ""},
        )

        self.assert_refused(response)
        self.playlist.refresh_from_db()
        self.assertNotEqual(self.playlist.name, "Stolen")

    def test_delete_playlist_refuses_a_stranger(self):
        response = self.client.delete(
            reverse("delete_playlist", args=[self.playlist.pk])
        )

        self.assert_refused(response)
        self.assertTrue(
            type(self.playlist).objects.filter(pk=self.playlist.pk).exists()
        )

    # --- content ----------------------------------------------------------------

    def test_create_content_refuses_a_stranger(self):
        response = self.post_json(
            reverse("create_content", args=[self.playlist.pk]),
            {"title": "Injected", "resource_file_id": self.resource_file.pk},
        )

        self.assert_refused(response)
        self.assertFalse(Content.objects.filter(title="Injected").exists())

    def test_render_create_from_resource_form_refuses_a_stranger(self):
        self.assert_refused(
            self.client.post(
                reverse(
                    "render_create_from_resource_form",
                    args=[self.playlist.pk, self.resource.pk],
                )
            )
        )

    def test_render_create_from_resource_resources_refuses_a_stranger(self):
        self.assert_refused(
            self.client.get(
                reverse(
                    "render_create_from_resource_resources",
                    args=[self.playlist.pk],
                )
            )
        )

    def test_display_content_info_refuses_a_stranger(self):
        self.assert_refused(
            self.client.get(reverse("display_content_info", args=[self.content.pk]))
        )

    def test_render_content_settings_form_refuses_a_stranger(self):
        self.assert_refused(
            self.client.get(
                reverse("render_content_settings_form", args=[self.content.pk])
            )
        )

    def test_update_content_refuses_a_stranger(self):
        response = self.post_json(
            reverse("update_content", args=[self.content.pk]),
            {
                "title": "Retitled",
                "description": "",
                "allow_definitions": True,
                "allow_notes": True,
                "allow_captions": True,
                "allow_fast_playback": True,
                "clips_only": False,
                "published": False,
            },
        )

        self.assert_refused(response)
        self.content.refresh_from_db()
        self.assertNotEqual(self.content.title, "Retitled")

    def test_delete_content_refuses_a_stranger(self):
        response = self.client.delete(reverse("delete_content", args=[self.content.pk]))

        self.assert_refused(response)
        self.assertTrue(Content.objects.filter(pk=self.content.pk).exists())

    # --- annotation sets --------------------------------------------------------

    def test_build_annotation_panel_refuses_a_stranger(self):
        self.assert_refused(
            self.client.get(
                reverse("get_annotation_panel", args=[self.annotation_set.pk])
            )
        )

    def test_export_annotation_set_refuses_a_stranger(self):
        self.assert_refused(
            self.client.get(
                reverse("export_annotation_set", args=[self.annotation_set.pk])
            )
        )

    def test_delete_annotation_set_refuses_a_stranger(self):
        response = self.client.delete(
            reverse("delete_annotation_set", args=[self.annotation_set.pk])
        )

        self.assert_refused(response)
        self.annotation_set.refresh_from_db()
        self.assertEqual(self.annotation_set.owner_id, self.owner.pk)

    def test_delete_annotation_set_is_not_reachable_by_get(self):
        """The hole that made this one-click: no method guard, so a link was enough."""
        login(self.client, self.owner)

        response = self.client.get(
            reverse("delete_annotation_set", args=[self.annotation_set.pk])
        )

        self.assertEqual(response.status_code, 405)
        self.annotation_set.refresh_from_db()
        self.assertEqual(self.annotation_set.owner_id, self.owner.pk)

    def test_create_annotation_set_refuses_a_stranger(self):
        response = self.post_json(
            reverse("create_annotation_set", args=[self.content.pk]),
            {"name": "Injected"},
        )

        self.assert_refused(response)
        self.assertFalse(AnnotationSet.objects.filter(name="Injected").exists())
        self.content.refresh_from_db()
        self.assertEqual(self.content.annotation_set_id, self.annotation_set.pk)

    def test_select_annotation_set_refuses_a_stranger(self):
        other_set = AnnotationSetFactory(resource=self.resource)

        response = self.post_json(
            reverse("select_annotation_set", args=[self.content.pk]),
            {"annotation_set_id": other_set.pk},
        )

        self.assert_refused(response)
        self.content.refresh_from_db()
        self.assertEqual(self.content.annotation_set_id, self.annotation_set.pk)

    def test_display_copy_from_annotation_set_option_refuses_a_stranger(self):
        self.assert_refused(
            self.client.get(
                reverse(
                    "display_copy_from_annotation_set_option", args=[self.content.pk]
                )
            )
        )

    def test_display_use_existing_annotation_set_option_refuses_a_stranger(self):
        self.assert_refused(
            self.client.get(
                reverse(
                    "display_use_existing_annotation_set_option",
                    args=[self.content.pk],
                )
            )
        )

    # --- tracks -----------------------------------------------------------------

    def test_update_track_refuses_a_stranger(self):
        response = self.post_json(
            reverse("update_track", args=[self.track.pk]),
            {"new_track_name": "Renamed"},
        )

        self.assert_refused(response)
        self.track.refresh_from_db()
        self.assertNotEqual(self.track.name, "Renamed")

    def test_create_track_refuses_a_stranger(self):
        response = self.post_json(
            reverse("create_track", args=[self.annotation_set.pk]),
            {"track_name": "Injected"},
        )

        self.assert_refused(response)
        self.assertFalse(Track.objects.filter(name="Injected").exists())

    def test_delete_track_refuses_a_stranger(self):
        second_track = TrackFactory(
            annotation_set=self.annotation_set, stack_position=1
        )

        response = self.client.delete(reverse("delete_track", args=[second_track.pk]))

        self.assert_refused(response)
        self.assertTrue(Track.objects.filter(pk=second_track.pk).exists())

    def test_update_track_positions_refuses_a_stranger(self):
        second_track = TrackFactory(
            annotation_set=self.annotation_set, stack_position=1
        )

        response = self.post_json(
            reverse("update_tracks_stack_positions", args=[self.annotation_set.pk]),
            {"track_ids": [second_track.pk, self.track.pk]},
        )

        self.assert_refused(response)
        second_track.refresh_from_db()
        self.assertEqual(second_track.stack_position, 1)

    def test_reordering_cannot_reach_across_annotation_sets(self):
        """A valid track id from another set is not a way in, even for its owner."""
        foreign_track = TrackFactory(
            annotation_set=AnnotationSetFactory(owner=self.owner), stack_position=0
        )
        login(self.client, self.owner)

        response = self.post_json(
            reverse("update_tracks_stack_positions", args=[self.annotation_set.pk]),
            {"track_ids": [foreign_track.pk, self.track.pk]},
        )

        self.assertEqual(response.status_code, 400)
        foreign_track.refresh_from_db()
        self.assertEqual(foreign_track.stack_position, 0)

    # --- subtitles --------------------------------------------------------------

    def test_get_editable_subtitles_refuses_a_stranger(self):
        self.assert_refused(
            self.client.get(reverse("get_editable_subtitles", args=[self.subtitle.pk]))
        )

    def test_update_subtitle_content_refuses_a_stranger(self):
        original = self.subtitle.subtitles_file.read()

        response = self.post_json(
            reverse("update_subtitle_content", args=[self.subtitle.pk]),
            {
                "cues": [
                    {
                        "type": "CUE",
                        "payload": "Overwritten",
                        "identifier": "1",
                        "start_time": "0:00:00.00",
                        "end_time": "0:00:01.00",
                        "cue_settings": None,
                    }
                ],
                "seconds_nudge": 0,
                "nudge_excluded_cues": [],
                "is_autosave": False,
            },
        )

        self.assert_refused(response)
        self.subtitle.refresh_from_db()
        self.subtitle.subtitles_file.seek(0)
        self.assertEqual(self.subtitle.subtitles_file.read(), original)

    # --- video editor -----------------------------------------------------------

    def test_video_editor_refuses_a_stranger(self):
        self.assert_refused(
            self.client.get(reverse("video_editor", args=[self.content.pk]))
        )


class EnrolledStudentTests(TestCase):
    """A student who can legitimately watch the content still cannot author on it.

    select_annotation_set was the sharpest case: gated on view rather than edit, so an
    enrolled student could swap the active annotation set for the whole class.
    """

    def setUp(self):
        self.owner = UserFactory(instructor=True)
        self.course = CourseFactory()
        self.playlist = PlaylistFactory(owner=self.owner, published=True)
        self.playlist.courses.add(self.course)
        self.resource_file = ResourceFileFactory()
        self.content = ContentFactory(
            playlist=self.playlist, resource_file=self.resource_file, published=True
        )
        self.annotation_set = AnnotationSetFactory(
            owner=self.owner, resource=self.resource_file.resource
        )
        self.content.annotation_set = self.annotation_set
        self.content.save()

        self.student = UserFactory(student=True)
        UserCourseFactory(
            user=self.student, course=self.course, yearterm=self.course.yearterm
        )
        login(self.client, self.student)

    def test_an_enrolled_student_can_watch(self):
        response = self.client.get(reverse("player", args=[self.content.pk]))

        self.assertEqual(response.status_code, 200)

    def test_an_enrolled_student_cannot_swap_the_active_annotation_set(self):
        other_set = AnnotationSetFactory(resource=self.resource_file.resource)

        response = self.client.post(
            reverse("select_annotation_set", args=[self.content.pk]),
            data=json.dumps({"annotation_set_id": other_set.pk}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        self.content.refresh_from_db()
        self.assertEqual(self.content.annotation_set_id, self.annotation_set.pk)

    def test_an_enrolled_student_cannot_open_the_editor(self):
        response = self.client.get(reverse("video_editor", args=[self.content.pk]))

        self.assertEqual(response.status_code, 403)

    def test_an_enrolled_student_cannot_read_the_annotation_set_directly(self):
        response = self.client.get(
            reverse("export_annotation_set", args=[self.annotation_set.pk])
        )

        self.assertEqual(response.status_code, 403)

    def test_a_student_cannot_create_a_playlist(self):
        response = self.client.post(
            reverse("create_playlist"),
            data=json.dumps({"name": "Student Playlist"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            type(self.playlist).objects.filter(name="Student Playlist").exists()
        )


class UnpublishedContentTests(TestCase):
    """A draft inside a playlist someone can read is still a draft.

    The playlist page hides unpublished rows from read-only users, but content ids are
    sequential, so every endpoint that resolves a content has to check for itself.
    """

    def setUp(self):
        self.owner = UserFactory(instructor=True)
        self.course = CourseFactory()
        self.playlist = PlaylistFactory(owner=self.owner, published=True)
        self.playlist.courses.add(self.course)
        self.resource_file = ResourceFileFactory()
        self.draft = ContentFactory(
            playlist=self.playlist, resource_file=self.resource_file, published=False
        )
        self.url_draft = ContentFactory(
            playlist=self.playlist,
            resource_file=None,
            resource=self.resource_file.resource,
            url="https://example.invalid/video.mp4",
            published=False,
        )

        self.enrolled_student = UserFactory(student=True)
        UserCourseFactory(
            user=self.enrolled_student,
            course=self.course,
            yearterm=self.course.yearterm,
        )
        self.granted_student = UserFactory(student=True)
        PlaylistUserAccessFactory(
            user=self.granted_student,
            playlist=self.playlist,
            playlist_role=PlaylistRole.STUDENT,
        )

    def assert_draft_is_refused(self, user, content):
        login(self.client, user)

        self.assertEqual(
            self.client.get(reverse("player", args=[content.pk])).status_code, 403
        )
        self.assertEqual(
            self.client.post(reverse("get_player_data", args=[content.pk])).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(
                reverse("display_content_info", args=[content.pk])
            ).status_code,
            403,
        )

    def test_an_enrolled_student_cannot_open_a_draft(self):
        self.assert_draft_is_refused(self.enrolled_student, self.draft)

    def test_a_granted_student_cannot_open_a_draft(self):
        self.assert_draft_is_refused(self.granted_student, self.draft)

    def test_an_enrolled_student_cannot_open_a_url_backed_draft(self):
        self.assert_draft_is_refused(self.enrolled_student, self.url_draft)

    def test_a_draft_mints_no_media_key(self):
        self.assertIsNone(self.enrolled_student.get_resource_filekey(self.draft))
        self.assertIsNone(self.granted_student.get_resource_filekey(self.draft))

    def test_a_url_backed_draft_yields_no_source_url(self):
        self.assertIsNone(self.enrolled_student.get_content_source_url(self.url_draft))

    def test_the_owner_can_still_open_their_draft(self):
        login(self.client, self.owner)

        self.assertEqual(
            self.client.get(reverse("player", args=[self.draft.pk])).status_code, 200
        )

    def test_publishing_the_draft_lets_a_student_in(self):
        self.draft.published = True
        self.draft.save()
        login(self.client, self.enrolled_student)

        self.assertEqual(
            self.client.get(reverse("player", args=[self.draft.pk])).status_code, 200
        )


class TeachingAssistantTests(TestCase):
    """B1: a TA may do everything an owner may, except delete or promote."""

    def setUp(self):
        self.owner = UserFactory(instructor=True)
        self.playlist = PlaylistFactory(owner=self.owner, published=True)
        self.resource_file = ResourceFileFactory()
        self.content = ContentFactory(
            playlist=self.playlist, resource_file=self.resource_file
        )
        self.annotation_set = AnnotationSetFactory(
            owner=self.owner, resource=self.resource_file.resource
        )
        self.content.annotation_set = self.annotation_set
        self.content.save()

        self.ta = UserFactory(instructor=True)
        PlaylistUserAccessFactory(
            playlist=self.playlist, user=self.ta, playlist_role=PlaylistRole.TA
        )
        login(self.client, self.ta)

    def test_a_ta_may_rename_the_playlist(self):
        response = self.client.post(
            reverse("update_playlist_settings", args=[self.playlist.pk]),
            data={"name": "Renamed by TA", "published": "on", "archived": ""},
        )

        self.assertEqual(response.status_code, 200)
        self.playlist.refresh_from_db()
        self.assertEqual(self.playlist.name, "Renamed by TA")

    def test_a_ta_may_assign_a_course(self):
        response = self.client.post(
            reverse("assign_playlist_to_course", args=[self.playlist.pk]),
            data=json.dumps(
                {
                    "dept": "SPAN",
                    "catalog_number": "101",
                    "sections": ["001"],
                    "year": "2026",
                    "semester": "5",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.playlist.courses.exists())

    def test_a_ta_may_open_the_editor(self):
        response = self.client.get(reverse("video_editor", args=[self.content.pk]))

        self.assertEqual(response.status_code, 200)

    def test_a_ta_may_edit_the_owners_annotation_set(self):
        self.assertTrue(self.annotation_set.can_be_edited_by(self.ta))

    def test_a_ta_may_not_delete_the_playlist(self):
        response = self.client.delete(
            reverse("delete_playlist", args=[self.playlist.pk])
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(
            type(self.playlist).objects.filter(pk=self.playlist.pk).exists()
        )

    def test_a_ta_may_not_promote_anyone_to_ta(self):
        self.assertFalse(self.playlist.can_grant_role(self.ta, PlaylistRole.TA))
        self.assertFalse(self.playlist.can_grant_role(self.ta, PlaylistRole.INSTRUCTOR))

    def test_a_ta_may_grant_read_only_roles(self):
        self.assertTrue(self.playlist.can_grant_role(self.ta, PlaylistRole.STUDENT))

    def test_a_ta_may_not_retire_the_owners_annotation_set(self):
        response = self.client.delete(
            reverse("delete_annotation_set", args=[self.annotation_set.pk])
        )

        self.assertEqual(response.status_code, 403)
        self.annotation_set.refresh_from_db()
        self.assertEqual(self.annotation_set.owner_id, self.owner.pk)


class CoInstructorTests(TestCase):
    """Fact 3: no view creates INSTRUCTOR rows yet, so this builds one directly."""

    def setUp(self):
        self.owner = UserFactory(instructor=True)
        self.playlist = PlaylistFactory(owner=self.owner)
        self.co_instructor = UserFactory(instructor=True)
        PlaylistUserAccessFactory(
            playlist=self.playlist,
            user=self.co_instructor,
            playlist_role=PlaylistRole.INSTRUCTOR,
        )

    def test_a_co_instructor_has_the_owners_write_access(self):
        self.assertTrue(self.playlist.can_be_edited_by(self.co_instructor))
        self.assertTrue(self.playlist.can_be_viewed_by(self.co_instructor))

    def test_a_co_instructor_may_not_delete_or_promote(self):
        self.assertFalse(self.playlist.can_be_administered_by(self.co_instructor))
        self.assertFalse(
            self.playlist.can_grant_role(self.co_instructor, PlaylistRole.TA)
        )


class OwnerHappyPathTests(TestCase):
    """The tightening must not break the flows it is meant to protect."""

    def setUp(self):
        self.owner = UserFactory(instructor=True)
        self.playlist = PlaylistFactory(owner=self.owner)
        self.resource_file = ResourceFileFactory()
        self.resource = self.resource_file.resource
        self.content = ContentFactory(
            playlist=self.playlist, resource_file=self.resource_file
        )
        self.annotation_set = AnnotationSetFactory(
            owner=self.owner, resource=self.resource, name="Original"
        )
        TrackFactory(annotation_set=self.annotation_set)
        self.content.annotation_set = self.annotation_set
        self.content.save()
        login(self.client, self.owner)

    def post_json(self, url, payload=None):
        return self.client.post(
            url, data=json.dumps(payload or {}), content_type="application/json"
        )

    def test_the_owner_may_open_the_editor(self):
        response = self.client.get(reverse("video_editor", args=[self.content.pk]))

        self.assertEqual(response.status_code, 200)

    def test_the_owner_may_retire_their_own_set(self):
        response = self.client.delete(
            reverse("delete_annotation_set", args=[self.annotation_set.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.annotation_set.refresh_from_db()
        self.assertIsNone(self.annotation_set.owner_id)
        self.assertTrue(self.annotation_set.previous_owner)

    def test_the_owner_may_copy_a_set_they_can_read(self):
        borrowed = AnnotationSetFactory(resource=self.resource, name="Someone else's")

        response = self.post_json(
            reverse("create_annotation_set", args=[self.content.pk]),
            {"name": "My copy", "annotation_set_id_to_copy": borrowed.pk},
        )

        self.assertEqual(response.status_code, 200)
        copy = AnnotationSet.objects.get(name="My copy")
        self.assertEqual(copy.owner_id, self.owner.pk)
        self.content.refresh_from_db()
        self.assertEqual(self.content.annotation_set_id, copy.pk)

    def test_copying_a_set_the_user_cannot_read_is_refused(self):
        unreadable = AnnotationSetFactory(resource=ResourceFactory())

        response = self.post_json(
            reverse("create_annotation_set", args=[self.content.pk]),
            {"name": "Stolen copy", "annotation_set_id_to_copy": unreadable.pk},
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(AnnotationSet.objects.filter(name="Stolen copy").exists())

    def test_the_owner_may_rename_their_set(self):
        response = self.post_json(
            reverse("update_annotation_set_name", args=[self.annotation_set.pk]),
            {"name": "Renamed"},
        )

        self.assertEqual(response.status_code, 200)
        self.annotation_set.refresh_from_db()
        self.assertEqual(self.annotation_set.name, "Renamed")

    def test_the_owner_may_select_a_readable_set_from_another_owner(self):
        borrowed = AnnotationSetFactory(resource=self.resource)

        response = self.post_json(
            reverse("select_annotation_set", args=[self.content.pk]),
            {"annotation_set_id": borrowed.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.content.refresh_from_db()
        self.assertEqual(self.content.annotation_set_id, borrowed.pk)

    def test_the_owner_may_select_an_orphaned_set(self):
        """Retiring a set must not strip it out of everyone else's chooser."""
        orphan = AnnotationSetFactory(resource=self.resource)
        orphan.orphan()

        response = self.post_json(
            reverse("select_annotation_set", args=[self.content.pk]),
            {"annotation_set_id": orphan.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.content.refresh_from_db()
        self.assertEqual(self.content.annotation_set_id, orphan.pk)

    def test_the_owner_may_create_content_from_an_accessible_resource(self):
        response = self.post_json(
            reverse("create_content", args=[self.playlist.pk]),
            {"title": "New content", "resource_file_id": self.resource_file.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            Content.objects.filter(playlist=self.playlist, title="New content").exists()
        )

    def test_creating_content_from_an_inaccessible_resource_is_refused(self):
        foreign_file = ResourceFileFactory()

        response = self.post_json(
            reverse("create_content", args=[self.playlist.pk]),
            {"title": "Off limits", "resource_file_id": foreign_file.pk},
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Content.objects.filter(title="Off limits").exists())

    def test_the_owner_may_view_and_delete_their_content(self):
        self.assertEqual(
            self.client.get(
                reverse("display_content_info", args=[self.content.pk])
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.delete(
                reverse("delete_content", args=[self.content.pk])
            ).status_code,
            200,
        )

    def test_the_owner_may_delete_their_playlist(self):
        response = self.client.delete(
            reverse("delete_playlist", args=[self.playlist.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            type(self.playlist).objects.filter(pk=self.playlist.pk).exists()
        )

    def test_an_instructor_may_create_a_playlist(self):
        response = self.post_json(
            reverse("create_playlist"), {"name": "Brand new playlist"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            type(self.playlist).objects.filter(name="Brand new playlist").exists()
        )
