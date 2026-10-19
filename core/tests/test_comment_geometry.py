"""Comment geometry: x, y, and width as percentages of the video frame, with no stored height."""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase
from django.test import TransactionTestCase

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


class CommentGeometryMigrationTests(TransactionTestCase):
    """0016 through the migration executor, so the RunPython sees real pre-0016 rows."""

    def _migrate(self, target):
        # A fresh executor each time: the loader caches the applied-migrations graph.
        MigrationExecutor(connection).migrate([("core", target)])

    def _historical_comment_model(self, target):
        return (
            MigrationExecutor(connection)
            .loader.project_state(("core", target))
            .apps.get_model("core", "CommentAnnotation")
        )

    def setUp(self):
        self.addCleanup(self._migrate, AFTER)
        self._migrate(BEFORE)
        self.track = TrackFactory()

    def test_corners_become_x_y_width(self):
        old_comment = self._historical_comment_model(BEFORE)
        corners = {
            "ordinary": (11.0, 12.0, 51.0, 52.0),
            # The dev seed and many real rows store the right edge left of the left one.
            "inverted": (42.0, 28.0, 20.0, 10.0),
            "off_frame": (95.0, -5.0, 130.0, 10.0),
            "too_low": (10.0, 99.0, 20.0, 110.0),
        }
        pks = {
            name: old_comment.objects.create(
                track_id=self.track.pk,
                top_left_x=x,
                top_left_y=y,
                bottom_right_x=right,
                bottom_right_y=bottom,
            ).pk
            for name, (x, y, right, bottom) in corners.items()
        }

        self._migrate(AFTER)

        geometry = {
            name: CommentAnnotation.objects.values_list("x", "y", "width").get(pk=pk)
            for name, pk in pks.items()
        }
        self.assertEqual(
            geometry,
            {
                "ordinary": (11.0, 12.0, 40.0),
                "inverted": (42.0, 28.0, 58.0),
                "off_frame": (65.0, 0.0, 35.0),
                "too_low": (10.0, 97.0, 10.0),
            },
        )

    def test_unapplying_restores_the_right_edge(self):
        self._migrate(AFTER)
        comment = CommentAnnotation.objects.create(
            track=self.track, x=10.0, y=20.0, width=30.0
        )

        self._migrate(BEFORE)

        old_comment = self._historical_comment_model(BEFORE)
        self.assertEqual(
            old_comment.objects.values_list(
                "top_left_x", "top_left_y", "bottom_right_x"
            ).get(pk=comment.pk),
            (10.0, 20.0, 40.0),
        )
