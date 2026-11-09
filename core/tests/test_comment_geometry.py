"""Comment geometry: x, y, and width as percentages of the video frame, with no stored height."""

from django.test import TestCase

from core.factories import CommentAnnotationFactory
from core.factories import TrackFactory
from core.models import CommentAnnotation

BEFORE = "0015_rename_blur_anchor_point"
AFTER = "0016_comment_x_y_width"


class CommentGeometryClampTests(TestCase):
    def test_in_range_geometry_is_stored_as_given(self):
        comment = CommentAnnotationFactory(x=12.345, y=20.0, width=40.0)
        comment.refresh_from_db()
        self.assertEqual((comment.x, comment.y, comment.width), (12.35, 20.0, 40.0))

    def test_width_is_held_to_the_frame(self):
        narrow = CommentAnnotationFactory(x=10.0, width=-22.0)
        wide = CommentAnnotationFactory(x=0.0, width=150.0)
        self.assertEqual(narrow.width, 3.0)
        self.assertEqual(wide.width, 100.0)

    def test_the_box_is_pulled_back_onto_the_frame(self):
        comment = CommentAnnotationFactory(x=90.0, y=120.0, width=30.0)
        self.assertEqual((comment.x, comment.y, comment.width), (70.0, 97.0, 30.0))

    def test_edits_keep_the_geometry(self):
        comment = CommentAnnotationFactory(x=5.0, y=6.0, width=50.0)
        edited = comment.edit(text="Changed")
        self.assertEqual((edited.x, edited.y, edited.width), (5.0, 6.0, 50.0))


class CommentImportTests(TestCase):
    def setUp(self):
        self.track = TrackFactory()

    def _import(self, **geometry):
        return CommentAnnotation.from_import(
            {"name": "c", "start": 1, "end": 2, "text": "t", **geometry}, self.track
        )

    def test_current_exports_round_trip(self):
        exported = CommentAnnotationFactory(x=11.0, y=12.0, width=40.0).to_player_json()
        imported = self._import(**{key: exported[key] for key in ("x", "y", "width")})
        self.assertEqual((imported.x, imported.y, imported.width), (11.0, 12.0, 40.0))

    def test_exports_made_with_corners_still_import(self):
        imported = self._import(
            top_left_x=11.0, top_left_y=12.0, bottom_right_x=51.0, bottom_right_y=52.0
        )
        self.assertEqual((imported.x, imported.y, imported.width), (11.0, 12.0, 40.0))

    def test_inverted_corners_import_running_to_the_right_edge(self):
        imported = self._import(
            top_left_x=42.0, top_left_y=28.0, bottom_right_x=20.0, bottom_right_y=10.0
        )
        self.assertEqual((imported.x, imported.y, imported.width), (42.0, 28.0, 58.0))
