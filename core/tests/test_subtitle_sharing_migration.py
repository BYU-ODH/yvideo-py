"""Tests for the 0020 data migration that attaches existing subtitle tracks to contents."""

import importlib

from django.apps import apps
from django.test import TestCase

from core.factories import ContentFactory
from core.factories import PlaylistFactory
from core.factories import ResourceFileFactory
from core.factories import SubtitleFactory

MIGRATION = importlib.import_module("core.migrations.0031_subtitle_sharing")


class AttachExistingTracksTests(TestCase):
    def test_every_content_keeps_showing_the_tracks_on_its_resource(self):
        resource_file = ResourceFileFactory()
        first, second = (
            ContentFactory(playlist=PlaylistFactory(), resource_file=resource_file)
            for _ in range(2)
        )
        unrelated = ContentFactory(
            playlist=PlaylistFactory(), resource_file=ResourceFileFactory()
        )
        tracks = [SubtitleFactory(resource=resource_file.resource) for _ in range(2)]

        MIGRATION.attach_existing_tracks(apps, None)

        for content in (first, second):
            self.assertCountEqual(content.subtitle_tracks.all(), tracks)
        self.assertFalse(unrelated.subtitle_tracks.exists())
