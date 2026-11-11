"""Deleting an annotation set removes it or transfers it to a remaining user."""

import json

from django.test import TestCase
from django.urls import reverse

from core.factories import AnnotationSetFactory
from core.factories import ContentFactory
from core.factories import UserFactory
from core.models import AnnotationSet


class AnnotationSetRemovalTests(TestCase):
    def setUp(self):
        self.annotation_set = AnnotationSetFactory()
        self.owner = self.annotation_set.owner
        self.owner_content = self._content_using_set(self.owner)
        self.url = reverse("delete_annotation_set", args=[self.annotation_set.pk])
        self.client.force_login(
            self.owner, backend="django.contrib.auth.backends.ModelBackend"
        )

    def _content_using_set(self, owner):
        return ContentFactory(
            playlist__owner=owner,
            resource_file=None,
            resource=self.annotation_set.resource,
            annotation_set=self.annotation_set,
        )

    def test_delete_removes_a_set_with_no_other_users(self):
        response = self.client.delete(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            AnnotationSet.objects.filter(pk=self.annotation_set.pk).exists()
        )
        self.owner_content.refresh_from_db()
        self.assertIsNone(self.owner_content.annotation_set_id)

    def test_delete_transfers_a_set_to_its_only_other_user(self):
        borrower = UserFactory(instructor=True)
        borrowed_content = self._content_using_set(borrower)

        response = self.client.delete(self.url)

        self.assertEqual(response.status_code, 200)
        self.annotation_set.refresh_from_db()
        self.assertEqual(self.annotation_set.owner_id, borrower.pk)
        borrowed_content.refresh_from_db()
        self.assertEqual(borrowed_content.annotation_set_id, self.annotation_set.pk)
        self.owner_content.refresh_from_db()
        self.assertIsNone(self.owner_content.annotation_set_id)

    def test_delete_is_forbidden_for_a_non_owner(self):
        borrower = UserFactory(instructor=True)
        borrowed_content = self._content_using_set(borrower)
        self.client.force_login(
            borrower, backend="django.contrib.auth.backends.ModelBackend"
        )

        response = self.client.delete(self.url)

        self.assertEqual(response.status_code, 403)
        self.annotation_set.refresh_from_db()
        self.assertEqual(self.annotation_set.owner_id, self.owner.pk)
        for content in (self.owner_content, borrowed_content):
            content.refresh_from_db()
            self.assertEqual(content.annotation_set_id, self.annotation_set.pk)

    def test_delete_transfers_a_set_to_the_specified_user(self):
        first_borrower = UserFactory(instructor=True)
        first_content = self._content_using_set(first_borrower)
        chosen_owner = UserFactory(instructor=True)
        chosen_content = self._content_using_set(chosen_owner)

        response = self.client.delete(
            self.url,
            data=json.dumps({"new_owner_id": chosen_owner.pk}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.annotation_set.refresh_from_db()
        self.assertEqual(self.annotation_set.owner_id, chosen_owner.pk)
        for content in (first_content, chosen_content):
            content.refresh_from_db()
            self.assertEqual(content.annotation_set_id, self.annotation_set.pk)
        self.owner_content.refresh_from_db()
        self.assertIsNone(self.owner_content.annotation_set_id)
