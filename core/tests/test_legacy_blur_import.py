"""How a legacy censor becomes a blur annotation.

The legacy app anchored a censor's geometry at the box's **center** and stored its keyframes as
`{arbitrary_key: [time, centerX, centerY, width, height]}`. This app stores the **top-left**
corner, one row per time, ordered by time. Nothing converted between the two until the importer
learned to, and taking the numbers verbatim puts every box `width/2` percent right and `height/2`
percent down from where its author placed it - which for a feature whose entire job is covering
copyrighted, violent, or explicit content means the bottom-right of the subject is exposed.

Nothing had been imported from the legacy server before the conversion landed, so these tests
guard the path rather than describing damage already in the database.

The other cases here are legacy quirks that the invariants added in migration 0004 turned into
hard failures or dead ends: arbitrary keys mean two keyframes can share a time (now a unique
index), `position: {}` was a legitimate legacy state for a censor created from the timeline
(now a blur that cannot render and, since the editor has no create-a-region gesture, cannot be
repaired by hand), and a censor's earliest keyframe was never required to coincide with its
start.
"""

import json

from django.test import TestCase

from core.factories import ContentFactory
from core.factories import UserFactory
from core.legacy_migration import LegacyMigrationRequest
from core.legacy_migration.service import LegacyMigrationService
from core.models import BlurAnnotation


def _censor(position, start=2.0, end=6.0):
    return {
        "type": "Censor",
        "layer": 1,
        "start": start,
        "end": end,
        "position": position,
    }


class LegacyCensorImportTests(TestCase):
    def setUp(self):
        self.owner = UserFactory(instructor=True)
        self.content = ContentFactory()
        self.request_obj = LegacyMigrationRequest.objects.create(
            requested_by=self.owner,
            target_owner=self.owner,
            migration_kind="collection",
            legacy_reference="legacy-collection-1",
        )
        self.service = LegacyMigrationService(require_catalog=False)

    def _import(self, *legacy_events):
        self.service._import_annotations(
            self.request_obj, self.content, list(legacy_events)
        )
        # The import builds its own AnnotationSet and maps it through LegacySourceMap rather
        # than attaching it to the Content, and each test imports exactly one censor.
        return BlurAnnotation.objects.get()

    def _stored(self, blur):
        return [(p.time, p.x, p.y, p.width, p.height) for p in blur.positions.all()]

    # --- the coordinate conversion -------------------------------------------

    def test_center_anchored_legacy_geometry_becomes_top_left(self):
        """The headline fix. Legacy center (40, 50) of a 20x30 box is top-left (30, 35).

        Deliberately asymmetric - x != y, width != height, and neither offset equals the other -
        so swapping x/y or width/height cannot produce the expected answer by accident.
        """
        blur = self._import(_censor({"0": [2, 40, 50, 20, 30]}))

        self.assertEqual(self._stored(blur), [(2.0, 30.0, 35.0, 20.0, 30.0)])

    def test_each_keyframe_is_converted_independently(self):
        blur = self._import(
            _censor({"0": [2, 40, 50, 20, 30], "1": [4, 60, 70, 10, 50]})
        )

        self.assertEqual(
            self._stored(blur),
            [(2.0, 30.0, 35.0, 20.0, 30.0), (4.0, 55.0, 45.0, 10.0, 50.0)],
        )

    def test_a_box_centered_near_an_edge_is_clamped_not_dropped(self):
        """Converting can push the corner negative; save() pulls it back onto the frame.

        Legacy allowed a box centered close enough to an edge that half of it hung off. That is
        a real censor covering real content, so the import keeps it against the edge rather than
        discarding it.
        """
        blur = self._import(_censor({"0": [2, 5, 4, 30, 20]}))

        self.assertEqual(self._stored(blur), [(2.0, 0.0, 0.0, 30.0, 20.0)])

    def test_conversion_survives_the_round_trip_through_json(self):
        """Legacy annotations arrive as a JSON string, and floats are common in real data.

        Halving an odd width lands the corner on a half-cent boundary, which happens often in
        legacy data, so the exact values here are worth pinning down rather than rounding off in
        the assertion: 33.33 - 11.11/2 is 27.775, which the nearest double represents as very
        slightly less, so save() stores 27.77 rather than 27.78. A hundredth of a percent of a
        frame is far below anything visible; what matters is that it is deterministic.
        """
        blur = self._import(
            *json.loads(json.dumps([_censor({"0": [2.5, 33.33, 44.44, 11.11, 22.22]})]))
        )

        stored = blur.positions.get()
        self.assertEqual(stored.x, 27.77)
        self.assertEqual(stored.y, 33.33)

    # --- legacy quirks the 0004 invariants made load-bearing -----------------

    def test_two_keyframes_at_the_same_time_do_not_abort_the_import(self):
        """Legacy keys are arbitrary ids, so nothing stopped two sharing a time.

        The unique (blur_annotation, time) index added in migration 0004 turns that into an
        IntegrityError, and _import_annotations does not run in a transaction - so an
        unrecoverable exception here abandons a migration job part-way through a playlist.

        The duplicated time is deliberately not the earliest, so collapsing it is the only thing
        under test rather than being confounded with the start-time pinning below.
        """
        blur = self._import(
            _censor(
                {
                    "0": [2, 40, 50, 20, 30],
                    "1": [3, 60, 70, 10, 50],
                    "2": [3, 20, 30, 10, 10],
                }
            )
        )

        self.assertEqual([p.time for p in blur.positions.all()], [2.0, 3.0])

    def test_times_colliding_only_after_quantization_do_not_abort_the_import(self):
        """save() rounds to 2dp, so distinct legacy times can land on the same row."""
        blur = self._import(
            _censor(
                {
                    "0": [2, 40, 50, 20, 30],
                    "1": [3.001, 60, 70, 10, 50],
                    "2": [3.002, 20, 30, 10, 10],
                }
            )
        )

        self.assertEqual([p.time for p in blur.positions.all()], [2.0, 3.0])

    def test_a_censor_with_no_keyframes_still_gets_geometry(self):
        """`position: {}` was how the legacy timeline's + button created a censor.

        Such a blur has no geometry at all: it silently does not render, and since the editor
        was deliberately left with no gesture that creates a region, there is no way for a user
        to give it one. Importing a default box makes it visible and therefore fixable.
        """
        blur = self._import(_censor({}))

        self.assertEqual(blur.positions.count(), 1)
        position = blur.positions.get()
        self.assertEqual(position.time, blur.start_time)
        self.assertGreater(position.width, 0)
        self.assertGreater(position.height, 0)

    def test_malformed_keyframes_are_skipped_rather_than_failing_the_import(self):
        blur = self._import(
            _censor(
                {
                    "0": [2, 40, 50, 20, 30],
                    "1": "not a list",
                    "2": [4, 60],
                    "3": [5, "wide", 70, 10, 50],
                    "4": None,
                }
            )
        )

        self.assertEqual(self._stored(blur), [(2.0, 30.0, 35.0, 20.0, 30.0)])

    def test_the_earliest_keyframe_is_pinned_to_the_censors_start(self):
        """Invariant I5. Legacy never guaranteed a keyframe at the event's start.

        Without this the first row is unreachable: get_position_locators skips it (position one
        is meant to coincide with the item's own left handle, so a dot there would block it) and
        the panel renders its time readonly - so the user can neither retime nor delete it.
        """
        blur = self._import(_censor({"0": [3, 40, 50, 20, 30]}, start=2.0, end=6.0))

        self.assertEqual(blur.positions.first().time, 2.0)
        # Retimed, not replaced: the author's geometry is what should show at the start.
        self.assertEqual(blur.positions.first().x, 30.0)

    def test_keyframes_past_the_censors_end_are_dropped(self):
        """They could never have been seen, and their dots would sit off the end of the bar."""
        blur = self._import(
            _censor({"0": [2, 40, 50, 20, 30], "1": [9, 60, 70, 10, 50]})
        )

        self.assertEqual([p.time for p in blur.positions.all()], [2.0])

    def test_only_the_latest_keyframe_before_the_start_survives(self):
        """That one is the geometry showing when the censor begins; earlier ones are history.

        Matches what migration 0004 did to blurs imported before this conversion existed, so a
        re-import cannot produce data the migration would have cleaned up.
        """
        blur = self._import(
            _censor(
                {
                    "0": [0.5, 10, 10, 20, 30],
                    "1": [1.5, 40, 50, 20, 30],
                    "2": [4, 60, 70, 10, 50],
                },
                start=2.0,
                end=6.0,
            )
        )

        self.assertEqual([p.time for p in blur.positions.all()], [2.0, 4.0])
        # The survivor is the one from t=1.5, retimed to the start by ensure_first_position.
        self.assertEqual(blur.positions.first().x, 30.0)

    def test_a_keyframe_already_at_the_start_wins_over_an_earlier_one(self):
        """Retiming onto an occupied time would collide with the unique index."""
        blur = self._import(
            _censor({"0": [1.0, 10, 10, 20, 30], "1": [2.0, 40, 50, 20, 30]})
        )

        self.assertEqual([p.time for p in blur.positions.all()], [2.0])
        self.assertEqual(blur.positions.get().x, 30.0)
