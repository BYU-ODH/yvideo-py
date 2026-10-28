"""Tests for the 0014 data migration that folds the legacy TA role into COLLABORATOR."""

import importlib

from django.apps import apps
from django.test import TestCase

from core.factories import PlaylistUserAccessFactory
from core.models import PlaylistRole
from core.models import PlaylistUserAccess

MIGRATION = importlib.import_module("core.migrations.0014_merge_ta_into_collaborator")
LEGACY_TA = 1


class MergeTaIntoCollaboratorTests(TestCase):
    def test_tas_become_collaborators_and_students_are_untouched(self):
        ta = PlaylistUserAccessFactory()
        PlaylistUserAccess.objects.filter(pk=ta.pk).update(playlist_role=LEGACY_TA)
        collaborator = PlaylistUserAccessFactory(
            playlist_role=PlaylistRole.COLLABORATOR
        )
        student = PlaylistUserAccessFactory(playlist_role=PlaylistRole.STUDENT)

        MIGRATION.tas_become_collaborators(apps, None)

        for access, expected in (
            (ta, PlaylistRole.COLLABORATOR),
            (collaborator, PlaylistRole.COLLABORATOR),
            (student, PlaylistRole.STUDENT),
        ):
            access.refresh_from_db()
            self.assertEqual(access.playlist_role, expected)
