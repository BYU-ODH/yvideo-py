import json

from django.test import TestCase
from django.test import override_settings
from django.urls import reverse

from ..factories import AnnotationSetFactory
from ..factories import CommentAnnotationFactory
from ..factories import ContentFactory
from ..factories import PlaylistFactory
from ..factories import ResourceFactory
from ..factories import ResourceFileFactory
from ..factories import TrackFactory
from ..factories import UserFactory
from ..models import BlankAnnotation
from ..models import BlurAnnotation
from ..models import BlurAnnotationPosition
from ..models import Clip
from ..models import CommentAnnotation
from ..models import MuteAnnotation
from ..models import PauseAnnotation
from ..models import SkipAnnotation


class AnnotationHistoryModelTests(TestCase):
    def setUp(self):
        self.track = TrackFactory()

    def test_edit_copies_every_annotation_type_specific_field(self):
        annotations = [
            SkipAnnotation.objects.create(
                track=self.track, description="skip", message="Heads up"
            ),
            MuteAnnotation.objects.create(track=self.track, description="mute"),
            BlankAnnotation.objects.create(
                track=self.track, description="blank", type="w"
            ),
            PauseAnnotation.objects.create(
                track=self.track, description="pause", message="Consider this"
            ),
            CommentAnnotation.objects.create(
                track=self.track,
                description="comment",
                text="Original text",
                top_left_x=11,
                top_left_y=12,
                bottom_right_x=51,
                bottom_right_y=52,
                font_size_in_rem=1.5,
                font_color="abcdef",
            ),
            Clip.objects.create(track=self.track, description="clip"),
        ]

        for original in annotations:
            with self.subTest(annotation_type=original.annotation_type):
                original_values = {
                    field.name: getattr(original, field.name)
                    for field in original._meta.fields
                    if field.name
                    not in {
                        "id",
                        "created_at",
                        "updated_at",
                        "prev",
                        "next",
                        "active",
                        "name",
                    }
                }
                edited = original.edit(name="Edited")

                original.refresh_from_db()
                self.assertFalse(original.active)
                self.assertEqual(original.next_id, edited.id)
                self.assertTrue(edited.active)
                self.assertEqual(edited.prev_id, original.id)
                self.assertEqual(edited.name, "Edited")
                for field_name, value in original_values.items():
                    self.assertEqual(getattr(edited, field_name), value)

    def test_edit_copies_blur_positions_to_each_version(self):
        blur = BlurAnnotation.objects.create(
            track=self.track,
            name="Logo",
            description="Blur a logo",
            start_time=2,
            end_time=8,
        )
        position = BlurAnnotationPosition.objects.create(
            blur_annotation=blur,
            time=4,
            x=10,
            y=20,
            width=30,
            height=40,
        )

        edited = blur.edit(name="Edited logo")
        copied_position = edited.positions.get()

        self.assertNotEqual(copied_position.id, position.id)
        self.assertEqual(copied_position.time, position.time)
        self.assertEqual(copied_position.x, position.x)
        self.assertEqual(copied_position.y, position.y)
        self.assertEqual(copied_position.width, position.width)
        self.assertEqual(copied_position.height, position.height)

    def test_undo_redo_and_edit_after_undo_manage_the_chain(self):
        root = CommentAnnotationFactory(track=self.track, name="Root")
        first_edit = root.edit(name="First edit")
        second_edit = first_edit.edit(name="Second edit")

        self.assertEqual(second_edit.undo(), first_edit)
        first_edit.refresh_from_db()
        second_edit.refresh_from_db()
        self.assertTrue(first_edit.active)
        self.assertFalse(second_edit.active)

        self.assertEqual(first_edit.redo(), second_edit)
        first_edit.refresh_from_db()
        second_edit.refresh_from_db()
        self.assertFalse(first_edit.active)
        self.assertTrue(second_edit.active)

        self.assertEqual(second_edit.undo(), first_edit)
        replacement = first_edit.edit(name="Replacement edit")

        first_edit.refresh_from_db()
        self.assertEqual(first_edit.next_id, replacement.id)
        self.assertFalse(CommentAnnotation.objects.filter(id=second_edit.id).exists())
        self.assertIsNone(replacement.next_id)


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    }
)
class AnnotationHistoryViewTests(TestCase):
    def setUp(self):
        self.owner = UserFactory(instructor=True)
        self.resource = ResourceFactory()
        self.playlist = PlaylistFactory(owner=self.owner)
        self.resource_file = ResourceFileFactory(resource=self.resource)
        self.annotation_set = AnnotationSetFactory(
            owner=self.owner,
            resource=self.resource,
        )
        self.content = ContentFactory(
            playlist=self.playlist,
            resource=self.resource,
            resource_file=self.resource_file,
            annotation_set=self.annotation_set,
        )
        self.track = TrackFactory(annotation_set=self.annotation_set)
        self.annotation = CommentAnnotationFactory(
            track=self.track,
            name="Original",
            text="Before",
        )
        self.client.force_login(
            self.owner,
            backend="django.contrib.auth.backends.ModelBackend",
        )

    def update_annotation(self, annotation=None, **overrides):
        annotation = annotation or self.annotation
        payload = {
            "content_id": self.content.id,
            "annotation_name": "Updated",
            "start_time": 3.5,
            "end_time": 9.5,
            "description": "Updated description",
            "text": "After",
            "top_left_x": 1,
            "top_left_y": 2,
            "bottom_right_x": 80,
            "bottom_right_y": 40,
            "font_size_in_rem": 2,
            "font_color": "123abc",
        }
        payload.update(overrides)
        return self.client.post(
            reverse(
                "update_annotation", args=[self.content.id, "comment", annotation.id]
            ),
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_update_creates_a_version_and_returns_all_synchronized_fragments(self):
        second_track = TrackFactory(annotation_set=self.annotation_set)

        response = self.update_annotation(track_id=second_track.id)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.annotation.refresh_from_db()
        updated = CommentAnnotation.objects.get(id=data["annotation_id"])
        self.assertNotEqual(updated.id, self.annotation.id)
        self.assertFalse(self.annotation.active)
        self.assertEqual(self.annotation.next_id, updated.id)
        self.assertTrue(updated.active)
        self.assertEqual(updated.prev_id, self.annotation.id)
        self.assertEqual(updated.track, second_track)
        self.assertEqual(updated.name, "Updated")
        self.assertEqual(updated.text, "After")
        self.assertEqual(data["track_id"], second_track.id)
        self.assertIn(f'id="comment-{updated.id}"', data["item_html"])
        self.assertIn(f'id="comment-panel-item-{updated.id}"', data["panel_item_html"])
        self.assertIn(f'data-annotation-id="{updated.id}"', data["form_html"])

    def test_update_accepts_and_rerenders_editor_timestamp_values(self):
        response = self.update_annotation(
            start_time="0:00:03.50", end_time="0:00:09.50"
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        updated = CommentAnnotation.objects.get(id=data["annotation_id"])
        self.assertEqual(updated.start_time, 3.5)
        self.assertEqual(updated.end_time, 9.5)
        self.assertIn('value="0:00:03.50"', data["form_html"])
        self.assertIn('value="0:00:09.50"', data["form_html"])

    def test_undo_and_redo_return_the_new_active_version(self):
        update_response = self.update_annotation()
        updated_id = update_response.json()["annotation_id"]

        undo_response = self.client.post(
            reverse("undo_annotation", args=[self.content.id]),
            {"annotation_id": updated_id, "annotation_type": "comment"},
        )

        self.assertEqual(undo_response.status_code, 200)
        self.assertEqual(undo_response.json()["annotation_id"], self.annotation.id)
        self.annotation.refresh_from_db()
        updated = CommentAnnotation.objects.get(id=updated_id)
        self.assertTrue(self.annotation.active)
        self.assertFalse(updated.active)
        self.assertIn("redo-btn", undo_response.json()["form_html"])

        redo_response = self.client.post(
            reverse("redo_annotation", args=[self.content.id]),
            {"annotation_id": self.annotation.id, "annotation_type": "comment"},
        )

        self.assertEqual(redo_response.status_code, 200)
        self.assertEqual(redo_response.json()["annotation_id"], updated_id)
        self.annotation.refresh_from_db()
        updated.refresh_from_db()
        self.assertFalse(self.annotation.active)
        self.assertTrue(updated.active)

    def test_history_endpoint_rejects_annotation_from_another_set(self):
        other_set = AnnotationSetFactory(owner=self.owner, resource=self.resource)
        other_annotation = CommentAnnotationFactory(
            track=TrackFactory(annotation_set=other_set)
        )
        other_updated = other_annotation.edit(name="Other update")

        response = self.client.post(
            reverse("undo_annotation", args=[self.content.id]),
            {"annotation_id": other_updated.id, "annotation_type": "comment"},
        )

        self.assertEqual(response.status_code, 404)
        other_updated.refresh_from_db()
        self.assertTrue(other_updated.active)

    def test_history_endpoints_reject_a_user_who_cannot_edit_the_set(self):
        updated = self.annotation.edit(name="Updated")
        self.annotation.refresh_from_db()
        outsider = UserFactory(instructor=True)
        self.client.force_login(
            outsider,
            backend="django.contrib.auth.backends.ModelBackend",
        )

        for url_name, annotation_id in [
            ("undo_annotation", updated.id),
            ("redo_annotation", self.annotation.id),
        ]:
            with self.subTest(url_name=url_name):
                response = self.client.post(
                    reverse(url_name, args=[self.content.id]),
                    {"annotation_id": annotation_id, "annotation_type": "comment"},
                )

                self.assertEqual(response.status_code, 403)

        # Not merely refused - the chain is untouched, so the owner's active version is still
        # the one it was before the outsider tried.
        updated.refresh_from_db()
        self.annotation.refresh_from_db()
        self.assertTrue(updated.active)
        self.assertFalse(self.annotation.active)

    def test_form_endpoint_rejects_an_annotation_belonging_to_other_content(self):
        other_set = AnnotationSetFactory(owner=self.owner, resource=self.resource)
        other_annotation = CommentAnnotationFactory(
            track=TrackFactory(annotation_set=other_set)
        )

        response = self.client.get(
            reverse("load_annotation_form", args=["comment", other_annotation.id]),
            {"content_id": self.content.id},
        )

        # Owned by the same user, so this is not about permissions: the annotation simply is not
        # part of the content whose editor is asking for it.
        self.assertEqual(response.status_code, 404)

    def test_undo_restores_the_previous_versions_blur_positions(self):
        blur = BlurAnnotation.objects.create(
            track=self.track,
            name="Logo",
            start_time=0,
            end_time=10,
            active=True,
        )
        for time in (2, 6):
            BlurAnnotationPosition.objects.create(
                blur_annotation=blur, time=time, x=10, y=20, width=30, height=40
            )
        original_times = [position.time for position in blur.positions.all()]

        # A resize, which reconciles the new version's points against the smaller window.
        update_response = self.client.post(
            reverse("update_annotation", args=[self.content.id, "blur", blur.id]),
            data=json.dumps(
                {
                    "content_id": self.content.id,
                    "start_time": 4,
                    "end_time": 10,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(update_response.status_code, 200)
        updated_id = update_response.json()["annotation_id"]
        updated = BlurAnnotation.objects.get(id=updated_id)
        updated_times = [position.time for position in updated.positions.all()]
        self.assertNotEqual(updated_times, original_times)
        # The superseded version keeps its own copy - that copy is the whole point of undo.
        self.assertEqual(
            [position.time for position in blur.positions.all()], original_times
        )

        undo_response = self.client.post(
            reverse("undo_annotation", args=[self.content.id]),
            {"annotation_id": updated_id, "annotation_type": "blur"},
        )

        self.assertEqual(undo_response.status_code, 200)
        restored = BlurAnnotation.objects.get(id=undo_response.json()["annotation_id"])
        self.assertEqual(restored.id, blur.id)
        self.assertTrue(restored.active)
        self.assertEqual(
            [position.time for position in restored.positions.all()], original_times
        )

    def test_undo_discards_rig_edits_made_since_the_version_was_created(self):
        """Pins a known limitation rather than asserting desired behaviour.

        Blur geometry is written by upsert_blur_position, which edits the active version's
        positions in place and never calls edit(). Only a field save creates a version. So a rig
        drag made *after* a field save lands on the active version alone, and the next undo -
        which the user means as "take back that field save" - reverts the drag along with it.
        No history step ever corresponded to the drag, so nothing warns them.

        Order matters: dragging *before* a field save is safe, because edit() then snapshots the
        dragged geometry into the version it creates. It is the drag that follows a save that is
        exposed.

        Change this test when rig edits join the version chain; it exists so that change is a
        deliberate one and not a silent behaviour swap.
        """
        blur = BlurAnnotation.objects.create(
            track=self.track, name="Logo", start_time=0, end_time=10, active=True
        )
        # At time 0 so it is the point ensure_first_position pins to start_time; the drag below
        # then updates this row rather than adding a second one.
        BlurAnnotationPosition.objects.create(
            blur_annotation=blur, time=0, x=10, y=20, width=30, height=40
        )

        # A field save, which is the only thing that creates a version.
        rename = self.client.post(
            reverse("update_annotation", args=[self.content.id, "blur", blur.id]),
            data=json.dumps(
                {
                    "content_id": self.content.id,
                    "annotation_name": "Renamed logo",
                    "start_time": 0,
                    "end_time": 10,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(rename.status_code, 200)
        renamed = BlurAnnotation.objects.get(id=rename.json()["annotation_id"])
        self.assertEqual(renamed.positions.get().x, 10)

        # Now a rig drag on the new active version: geometry only, no version created. Kept
        # inside the frame so the model's clamping does not rewrite the value asserted on.
        drag = self.client.post(
            reverse("upsert_blur_position", args=[renamed.id]),
            data=json.dumps({"time": 0, "x": 55, "y": 50, "width": 30, "height": 40}),
            content_type="application/json",
        )
        self.assertEqual(drag.status_code, 200)
        renamed.refresh_from_db()
        self.assertIsNone(renamed.next_id)
        self.assertEqual(renamed.positions.get().x, 55)

        # One undo, meaning "take back the rename", silently takes the drag with it.
        undo = self.client.post(
            reverse("undo_annotation", args=[self.content.id]),
            {"annotation_id": renamed.id, "annotation_type": "blur"},
        )
        self.assertEqual(undo.status_code, 200)
        restored = BlurAnnotation.objects.get(id=undo.json()["annotation_id"])
        self.assertEqual(restored.id, blur.id)
        self.assertEqual(restored.name, "Logo")
        self.assertEqual(restored.positions.get().x, 10)

    def test_detail_form_has_icon_history_buttons_at_the_top(self):
        updated = self.annotation.edit(name="Updated")
        response = self.client.get(
            reverse("load_annotation_form", args=["comment", updated.id]),
            {"content_id": self.content.id},
        )

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertNotIn("#annotation-timeline", html)
        self.assertIn('class="undo-redo-toolbar"', html)
        self.assertIn('aria-label="Undo last change"', html)
        self.assertIn('aria-label="Redo last undone change"', html)
        self.assertIn("img/undo", html)
        self.assertIn("img/redo", html)
        self.assertLess(
            html.index("undo-redo-toolbar"), html.index("annotation-update-form")
        )
