from django.test import TestCase

from core.factories import AnnotationSetFactory
from core.factories import ContentFactory
from core.factories import PlaylistFactory
from core.factories import UserFactory
from core.models import AnnotationSet


class AnnotationSetDeletionOrReassignmentTests(TestCase):
    def setUp(self):
        self.owner = UserFactory(instructor=True)
        self.annotation_set = AnnotationSetFactory(owner=self.owner)
        self.owner_content = self._content_using_set(self.owner)

    def _content_using_set(self, user):
        return ContentFactory(
            playlist=PlaylistFactory(owner=user),
            resource_file=None,
            resource=self.annotation_set.resource,
            annotation_set=self.annotation_set,
        )

    def test_deletes_set_when_only_the_owner_uses_it(self):
        annotation_set_id = self.annotation_set.pk

        result = self.annotation_set.delete_or_reassign_ownership(self.owner)

        self.assertEqual(result, {"deleted": True, "reassigned": False})
        self.assertFalse(AnnotationSet.objects.filter(pk=annotation_set_id).exists())
        self.owner_content.refresh_from_db()
        self.assertIsNone(self.owner_content.annotation_set_id)

    def test_reassigns_set_to_the_only_other_user(self):
        other_user = UserFactory(instructor=True)
        other_content = self._content_using_set(other_user)

        result = self.annotation_set.delete_or_reassign_ownership(self.owner)

        self.assertEqual(result, {"deleted": False, "reassigned": True})
        self.annotation_set.refresh_from_db()
        self.assertEqual(self.annotation_set.owner, other_user)
        self.owner_content.refresh_from_db()
        self.assertIsNone(self.owner_content.annotation_set_id)
        other_content.refresh_from_db()
        self.assertEqual(other_content.annotation_set_id, self.annotation_set.pk)

    def test_reassigns_set_to_the_named_new_owner_among_multiple_users(self):
        other_user = UserFactory(instructor=True)
        other_content = self._content_using_set(other_user)
        # Choose the later-created user so the default first candidate is not enough.
        new_owner = UserFactory(instructor=True)
        new_owner_content = self._content_using_set(new_owner)

        result = self.annotation_set.delete_or_reassign_ownership(
            self.owner, new_owner=new_owner
        )

        self.assertEqual(result, {"deleted": False, "reassigned": True})
        self.annotation_set.refresh_from_db()
        self.assertEqual(self.annotation_set.owner, new_owner)
        self.owner_content.refresh_from_db()
        self.assertIsNone(self.owner_content.annotation_set_id)
        for content in (other_content, new_owner_content):
            with self.subTest(content_id=content.pk):
                content.refresh_from_db()
                self.assertEqual(content.annotation_set_id, self.annotation_set.pk)
