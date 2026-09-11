"""The model-layer permission rules from issue #111 that no inverted exploit covers.

These are the places where the work adds or expires access rather than removing it: the
term window that governs course-derived reads, ownerless annotation sets, resource-access
inheritance, and file-key expiry.
"""

from datetime import timedelta
import time

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.factories import AnnotationSetFactory
from core.factories import ContentFactory
from core.factories import CourseFactory
from core.factories import PlaylistFactory
from core.factories import PlaylistUserAccessFactory
from core.factories import ResourceAccessFactory
from core.factories import ResourceFactory
from core.factories import ResourceFileFactory
from core.factories import ResourceFileKeyFactory
from core.factories import SubtitleFactory
from core.factories import TrackFactory
from core.factories import UserCourseFactory
from core.factories import UserFactory
from core.models import TERM_GRACE_PERIOD
from core.models import AnnotationSet
from core.models import PlaylistRole
from core.models import ResourceFileKey
from core.models import User
from core.models import UserCourses
from core.models import YearTerm
from core.models import active_yearterms
from core.utils import estimate_current_yearterm


class ActiveYeartermsTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.term = YearTerm.objects.create(
            yearterm="20265",
            start_date_time=self.now - timedelta(days=30),
            end_date_time=self.now + timedelta(days=30),
        )

    def test_a_term_in_progress_is_active(self):
        self.assertIn("20265", active_yearterms(self.now))

    def test_the_window_covers_the_grace_period_on_both_sides(self):
        """Asserted on the term itself: active_yearterms falls back when nothing matches,
        which would otherwise mask an out-of-window answer with the estimate."""
        cases = [
            (self.term.start_date_time - TERM_GRACE_PERIOD - timedelta(days=1), False),
            (self.term.start_date_time - TERM_GRACE_PERIOD + timedelta(hours=1), True),
            (self.term.start_date_time + timedelta(days=1), True),
            (self.term.end_date_time - timedelta(days=1), True),
            (self.term.end_date_time + TERM_GRACE_PERIOD - timedelta(hours=1), True),
            (self.term.end_date_time + TERM_GRACE_PERIOD + timedelta(days=1), False),
        ]
        for moment, expected in cases:
            with self.subTest(moment=moment):
                self.assertEqual(self.term.is_active(moment), expected)

    def test_a_term_outside_its_window_is_not_returned_while_another_is_cached(self):
        neighbour = YearTerm.objects.create(
            yearterm="20271",
            start_date_time=self.term.end_date_time + timedelta(days=60),
            end_date_time=self.term.end_date_time + timedelta(days=150),
        )

        during_neighbour = active_yearterms(
            neighbour.start_date_time + timedelta(days=1)
        )

        self.assertEqual(during_neighbour, ["20271"])

    def test_two_consecutive_terms_overlap_at_a_boundary(self):
        YearTerm.objects.create(
            yearterm="20271",
            start_date_time=self.term.end_date_time + timedelta(days=7),
            end_date_time=self.term.end_date_time + timedelta(days=100),
        )

        both_active = active_yearterms(self.term.end_date_time + timedelta(days=1))

        self.assertEqual(set(both_active), {"20265", "20271"})

    def test_an_empty_cache_falls_back_to_the_estimate(self):
        """Failing closed would lock every student out of every course playlist."""
        YearTerm.objects.all().delete()

        with self.assertLogs("core.models", level="ERROR"):
            fallback = active_yearterms()

        self.assertEqual(fallback, [estimate_current_yearterm()])

    def test_a_stale_cache_falls_back_to_the_estimate(self):
        far_future = timezone.now() + timedelta(days=365 * 5)

        with self.assertLogs("core.models", level="ERROR"):
            fallback = active_yearterms(far_future)

        self.assertEqual(len(fallback), 1)


class CourseDerivedAccessTests(TestCase):
    """#360 / fact 2: the one place this work adds access rather than removing it."""

    def setUp(self):
        self.now = timezone.now()
        self.term = YearTerm.objects.create(
            yearterm="20265",
            start_date_time=self.now - timedelta(days=30),
            end_date_time=self.now + timedelta(days=30),
        )
        self.course = CourseFactory(yearterm="20265")
        self.owner = UserFactory(instructor=True)
        self.playlist = PlaylistFactory(owner=self.owner, published=True)
        self.playlist.courses.add(self.course)
        self.content = ContentFactory(playlist=self.playlist, published=True)
        self.student = UserFactory(student=True)
        self.enrollment = UserCourseFactory(
            user=self.student, course=self.course, yearterm="20265"
        )

    def test_a_currently_enrolled_student_can_view_the_content(self):
        self.assertTrue(self.content.can_be_viewed_by(self.student))
        self.assertTrue(self.playlist.can_be_viewed_by(self.student))

    def test_an_enrollment_from_an_expired_term_does_not_grant_access(self):
        self.enrollment.yearterm = "20241"
        self.enrollment.save()
        self.course.yearterm = "20241"
        self.course.save()

        self.assertFalse(self.content.can_be_viewed_by(self.student))

    def test_an_unpublished_playlist_is_not_visible_to_an_enrolled_student(self):
        self.playlist.published = False
        self.playlist.save()

        self.assertFalse(self.playlist.can_be_viewed_by(self.student))

    def test_an_archived_playlist_is_not_visible_to_an_enrolled_student(self):
        self.playlist.archived = True
        self.playlist.save()

        self.assertFalse(self.playlist.can_be_viewed_by(self.student))

    def test_the_owner_sees_an_unpublished_playlist(self):
        self.playlist.published = False
        self.playlist.save()

        self.assertTrue(self.playlist.can_be_viewed_by(self.owner))

    def test_a_student_in_a_different_course_has_no_access(self):
        other_student = UserFactory(student=True)
        UserCourseFactory(user=other_student, course=CourseFactory(yearterm="20265"))

        self.assertFalse(self.content.can_be_viewed_by(other_student))

    def test_enrollment_and_course_yearterms_agree(self):
        """A mismatch would silently grant or deny access; see #366."""
        for enrollment in UserCourses.objects.all():
            self.assertEqual(enrollment.yearterm, enrollment.course.yearterm)

    def test_the_listing_hides_playlists_the_student_could_not_open(self):
        unpublished = PlaylistFactory(owner=self.owner, published=False)
        unpublished.courses.add(self.course)
        archived = PlaylistFactory(owner=self.owner, published=True, archived=True)
        archived.courses.add(self.course)
        self.client.force_login(self.student)
        session = self.client.session
        session["oidc_id_token_expiration"] = time.time() + 3600
        session.save()

        response = self.client.get(reverse("playlists"))

        listed = [
            playlist["pk"]
            for yearterm in response.context["assigned_courses_by_yearterm"]
            for course in yearterm["playlists_by_course"]
            for playlist in course["playlists"]
        ]
        self.assertIn(self.playlist.pk, listed)
        self.assertNotIn(unpublished.pk, listed)
        self.assertNotIn(archived.pk, listed)


class ResourceAccessTests(TestCase):
    def setUp(self):
        self.instructor = UserFactory(instructor=True)
        self.granted_resource = ResourceFactory()
        ResourceAccessFactory(user=self.instructor, resource=self.granted_resource)
        self.ungranted_resource = ResourceFactory()
        self.library_resource = ResourceFactory(checked_out_from_hbll=True)

    def test_an_explicit_grant_gives_access(self):
        self.assertTrue(self.instructor.can_access_resource(self.granted_resource))

    def test_no_grant_means_no_access(self):
        self.assertFalse(self.instructor.can_access_resource(self.ungranted_resource))

    def test_every_instructor_reaches_a_byu_library_resource(self):
        self.assertTrue(self.library_resource.belongs_to_byu_library)
        self.assertTrue(self.instructor.can_access_resource(self.library_resource))

    def test_other_byu_library_counts_too(self):
        other = ResourceFactory(checked_out_from_other_byu_library=True)

        self.assertTrue(self.instructor.can_access_resource(other))

    def test_a_student_does_not_reach_a_byu_library_resource(self):
        student = UserFactory(student=True)

        self.assertFalse(student.can_access_resource(self.library_resource))

    def test_a_lab_assistant_reaches_everything(self):
        lab_assistant = UserFactory(student=True, lab_assistant=True)

        self.assertTrue(lab_assistant.can_access_resource(self.ungranted_resource))

    def test_a_lab_assistant_cannot_own_playlists(self):
        lab_assistant = UserFactory(student=True, lab_assistant=True)

        self.assertFalse(lab_assistant.is_instructor)

    def test_a_ta_inherits_every_grant_the_instructor_holds(self):
        ta = UserFactory(instructor=True)
        PlaylistUserAccessFactory(
            playlist=PlaylistFactory(owner=self.instructor),
            user=ta,
            playlist_role=PlaylistRole.TA,
        )

        self.assertTrue(ta.can_access_resource(self.granted_resource))
        self.assertFalse(ta.can_access_resource(self.ungranted_resource))

    def test_a_read_only_member_inherits_nothing(self):
        student = UserFactory(student=True)
        PlaylistUserAccessFactory(
            playlist=PlaylistFactory(owner=self.instructor),
            user=student,
            playlist_role=PlaylistRole.STUDENT,
        )

        self.assertFalse(student.can_access_resource(self.granted_resource))

    def test_the_library_listing_matches_the_predicate(self):
        from core.views import accessible_resources

        visible = set(accessible_resources(self.instructor))

        self.assertEqual(visible, {self.granted_resource, self.library_resource})


class OrphanedAnnotationSetTests(TestCase):
    def setUp(self):
        self.owner = UserFactory(
            instructor=True, first_name="Ada", last_name="Lovelace", netid="alovelace"
        )
        self.resource_file = ResourceFileFactory()
        self.resource = self.resource_file.resource
        ResourceAccessFactory(user=self.owner, resource=self.resource)
        self.annotation_set = AnnotationSetFactory(
            owner=self.owner, resource=self.resource, name="Ada's Annotations"
        )
        self.track = TrackFactory(annotation_set=self.annotation_set)
        self.borrower = UserFactory(instructor=True)
        ResourceAccessFactory(user=self.borrower, resource=self.resource)
        self.borrowed_content = ContentFactory(
            playlist=PlaylistFactory(owner=self.borrower),
            resource_file=self.resource_file,
        )
        self.borrowed_content.annotation_set = self.annotation_set
        self.borrowed_content.save()

    def test_orphaning_keeps_the_row_and_records_the_previous_owner(self):
        self.annotation_set.orphan()

        self.annotation_set.refresh_from_db()
        self.assertIsNone(self.annotation_set.owner_id)
        self.assertEqual(self.annotation_set.previous_owner, "Ada Lovelace (alovelace)")
        self.assertTrue(
            AnnotationSet.objects.filter(pk=self.annotation_set.pk).exists()
        )

    def test_an_orphaned_set_stays_the_active_set_of_borrowing_content(self):
        self.annotation_set.orphan()

        self.borrowed_content.refresh_from_db()
        self.assertEqual(
            self.borrowed_content.annotation_set_id, self.annotation_set.pk
        )

    def test_an_orphaned_set_is_still_readable_and_copyable(self):
        self.annotation_set.orphan()

        self.assertTrue(self.annotation_set.can_be_read_by(self.borrower))

    def test_an_orphaned_set_is_frozen_even_for_the_user_who_retired_it(self):
        self.annotation_set.orphan()

        self.assertFalse(self.annotation_set.can_be_edited_by(self.owner))

    def test_a_superuser_can_still_edit_an_orphaned_set(self):
        self.annotation_set.orphan()

        self.assertTrue(self.annotation_set.can_be_edited_by(UserFactory(admin=True)))

    def test_str_does_not_raise_on_an_orphaned_set(self):
        self.annotation_set.orphan()

        self.assertIn("no owner", str(self.annotation_set))
        self.assertIn("Ada Lovelace", str(self.annotation_set))

    def test_the_label_degrades_gracefully_with_no_previous_owner(self):
        self.annotation_set.owner = None
        self.annotation_set.save()

        self.assertEqual(self.annotation_set.owner_label(), "no owner")
        self.assertIn("no owner", str(self.annotation_set))

    def test_orphaning_twice_under_the_same_name_leaves_two_distinguishable_rows(self):
        """SQL treats NULLs as distinct, so (name, resource, NULL) permits duplicates."""
        self.annotation_set.orphan()
        recreated = AnnotationSetFactory(
            owner=self.owner, resource=self.resource, name="Ada's Annotations"
        )
        recreated.orphan()

        orphans = AnnotationSet.objects.filter(
            resource=self.resource, owner__isnull=True, name="Ada's Annotations"
        )
        self.assertEqual(orphans.count(), 2)
        self.assertNotEqual(*[orphan.created_at for orphan in orphans])

    def test_deleting_the_owner_orphans_rather_than_destroys(self):
        self.owner.delete()

        self.annotation_set.refresh_from_db()
        self.assertIsNone(self.annotation_set.owner_id)
        self.assertEqual(self.annotation_set.previous_owner, "Ada Lovelace (alovelace)")

    def test_deleting_the_owner_leaves_the_tracks_in_place(self):
        self.owner.delete()

        self.assertTrue(type(self.track).objects.filter(pk=self.track.pk).exists())

    def test_a_name_may_be_reused_by_a_different_owner_on_the_same_resource(self):
        duplicate = AnnotationSetFactory(
            owner=self.borrower, resource=self.resource, name="Ada's Annotations"
        )

        self.assertNotEqual(duplicate.pk, self.annotation_set.pk)


class AnnotationSetSharingTests(TestCase):
    def setUp(self):
        self.owner = UserFactory(instructor=True)
        self.resource = ResourceFactory()
        ResourceAccessFactory(user=self.owner, resource=self.resource)
        self.annotation_set = AnnotationSetFactory(
            owner=self.owner, resource=self.resource
        )

    def test_an_instructor_with_resource_access_may_read_but_not_edit(self):
        borrower = UserFactory(instructor=True)
        ResourceAccessFactory(user=borrower, resource=self.resource)

        self.assertTrue(self.annotation_set.can_be_read_by(borrower))
        self.assertFalse(self.annotation_set.can_be_edited_by(borrower))

    def test_an_instructor_without_resource_access_may_not_read(self):
        stranger = UserFactory(instructor=True)

        self.assertFalse(self.annotation_set.can_be_read_by(stranger))

    def test_a_student_may_not_read_annotation_sets(self):
        """D6 falls out of D1: can_access_resource is False for every plain student."""
        student = UserFactory(student=True)

        self.assertFalse(self.annotation_set.can_be_read_by(student))

    def test_the_chooser_lists_readable_sets_and_marks_the_read_only_ones(self):
        from core.views_video_editor import annotation_set_choices

        borrower = UserFactory(instructor=True)
        ResourceAccessFactory(user=borrower, resource=self.resource)
        own_set = AnnotationSetFactory(owner=borrower, resource=self.resource)
        content = ContentFactory(
            playlist=PlaylistFactory(owner=borrower),
            resource_file=ResourceFileFactory(resource=self.resource),
        )

        choices = {
            choice["annotation_set"].pk: choice["can_edit"]
            for choice in annotation_set_choices(content, borrower)
        }

        self.assertTrue(choices[own_set.pk])
        self.assertFalse(choices[self.annotation_set.pk])


class SubtitlePermissionTests(TestCase):
    def setUp(self):
        self.owner = UserFactory(instructor=True)
        self.resource = ResourceFactory()
        ResourceAccessFactory(user=self.owner, resource=self.resource)
        self.subtitle = SubtitleFactory(resource=self.resource, owner=self.owner)

    def test_the_owner_may_edit(self):
        self.assertTrue(self.subtitle.can_be_edited_by(self.owner))

    def test_the_owners_ta_may_edit(self):
        ta = UserFactory(instructor=True)
        PlaylistUserAccessFactory(
            playlist=PlaylistFactory(owner=self.owner),
            user=ta,
            playlist_role=PlaylistRole.TA,
        )

        self.assertTrue(self.subtitle.can_be_edited_by(ta))

    def test_an_instructor_with_resource_access_may_read_but_not_edit(self):
        borrower = UserFactory(instructor=True)
        ResourceAccessFactory(user=borrower, resource=self.resource)

        self.assertTrue(self.subtitle.can_be_read_by(borrower))
        self.assertFalse(self.subtitle.can_be_edited_by(borrower))

    def test_a_student_may_not_read_subtitles_outside_the_player(self):
        self.assertFalse(self.subtitle.can_be_read_by(UserFactory(student=True)))


class ResourceFileKeyTests(TestCase):
    def setUp(self):
        self.owner = UserFactory(instructor=True)
        self.resource_file = ResourceFileFactory()
        self.content = ContentFactory(
            playlist=PlaylistFactory(owner=self.owner),
            resource_file=self.resource_file,
        )

    def test_a_fresh_key_is_usable_by_its_own_user(self):
        key = self.owner.get_resource_filekey(self.content)

        self.assertTrue(key.can_be_used_by(self.owner))

    def test_a_key_is_not_usable_by_another_user(self):
        key = self.owner.get_resource_filekey(self.content)

        self.assertFalse(key.can_be_used_by(UserFactory(instructor=True)))

    def test_an_expired_key_is_refused(self):
        key = ResourceFileKeyFactory(user=self.owner, resource_file=self.resource_file)
        ResourceFileKey.objects.filter(pk=key.pk).update(
            created_at=timezone.now() - timedelta(hours=25)
        )
        key.refresh_from_db()

        self.assertTrue(key.is_expired())
        self.assertFalse(key.can_be_used_by(self.owner))

    def test_an_expired_key_is_replaced_rather_than_reused(self):
        stale = ResourceFileKeyFactory(
            user=self.owner, resource_file=self.resource_file
        )
        ResourceFileKey.objects.filter(pk=stale.pk).update(
            created_at=timezone.now() - timedelta(hours=25)
        )

        fresh = self.owner.get_resource_filekey(self.content)

        self.assertNotEqual(fresh.pk, stale.pk)
        self.assertFalse(fresh.is_expired())

    def test_expired_keys_are_pruned_rather_than_accumulating(self):
        stale = ResourceFileKeyFactory(
            user=self.owner, resource_file=self.resource_file
        )
        ResourceFileKey.objects.filter(pk=stale.pk).update(
            created_at=timezone.now() - timedelta(hours=25)
        )

        self.owner.get_resource_filekey(self.content)

        self.assertFalse(ResourceFileKey.objects.filter(pk=stale.pk).exists())

    def test_a_valid_key_is_reused_within_the_window(self):
        first = self.owner.get_resource_filekey(self.content)
        second = self.owner.get_resource_filekey(self.content)

        self.assertEqual(first.pk, second.pk)

    def test_no_key_is_issued_for_content_the_user_cannot_view(self):
        stranger = UserFactory(student=True)

        self.assertIsNone(stranger.get_resource_filekey(self.content))


class SpoofingCarriesWriteAccessTests(TestCase):
    """A6: a spoofed session holds the spoofed user's permissions, writes included."""

    def setUp(self):
        self.instructor = UserFactory(instructor=True)
        self.playlist = PlaylistFactory(owner=self.instructor)
        self.lab_assistant = UserFactory(student=True, lab_assistant=True)
        self.client.force_login(self.lab_assistant)
        session = self.client.session
        session["oidc_id_token_expiration"] = time.time() + 3600
        session["spoof_user_id"] = self.instructor.pk
        session.save()

    def test_a_spoofed_session_may_write_as_the_spoofed_instructor(self):
        response = self.client.post(
            reverse("update_playlist_settings", args=[self.playlist.pk]),
            data={"name": "Renamed while spoofing", "published": "", "archived": ""},
        )

        self.assertEqual(response.status_code, 200)
        self.playlist.refresh_from_db()
        self.assertEqual(self.playlist.name, "Renamed while spoofing")

    def test_the_lab_assistant_alone_could_not_have_done_that(self):
        self.assertFalse(self.playlist.can_be_edited_by(self.lab_assistant))


class InstructorCapabilityTests(TestCase):
    def test_privilege_level_makes_an_instructor(self):
        self.assertTrue(UserFactory(instructor=True).is_instructor)

    def test_the_override_makes_an_instructor(self):
        from core.models import PrivilegeLevel

        user = UserFactory(student=True)
        user.privilege_level_override = PrivilegeLevel.INSTRUCTOR
        user.save()

        self.assertTrue(user.is_instructor)

    def test_an_admin_is_an_instructor(self):
        self.assertTrue(UserFactory(admin=True).is_instructor)

    def test_a_student_is_not(self):
        self.assertFalse(UserFactory(student=True).is_instructor)

    def test_is_staff_alone_grants_nothing(self):
        """A3: is_staff is the Django admin flag, not a Y-Video role."""
        staff = User.objects.create_user(username="123456789", is_staff=True)
        content = ContentFactory(published=True)

        self.assertFalse(staff.is_instructor)
        self.assertFalse(content.can_be_viewed_by(staff))
