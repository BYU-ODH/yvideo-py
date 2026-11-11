from html.parser import HTMLParser
import json
import time
from unittest.mock import patch

from django.contrib.messages import get_messages
from django.template.loader import render_to_string
from django.test import TestCase
from django.urls import reverse

from core.factories import AnnotationSetFactory
from core.factories import ContentFactory
from core.factories import PlaylistFactory
from core.factories import TrackFactory
from core.factories import UserFactory
from core.models import AnnotationSet
from core.models import Track


class _OwnerSelectParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.select_count = 0
        self.option_values = []
        self.in_owner_select = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "select":
            self.in_owner_select = attrs.get("id") == "annotation-set-new-owner"
            if self.in_owner_select:
                self.select_count += 1
        elif tag == "option" and self.in_owner_select:
            self.option_values.append(attrs.get("value"))

    def handle_endtag(self, tag):
        if tag == "select":
            self.in_owner_select = False


class AnnotationSetDeletionTests(TestCase):
    def setUp(self):
        self.owner = UserFactory(instructor=True)
        self.annotation_set = AnnotationSetFactory(owner=self.owner)
        self.content = ContentFactory(
            playlist=PlaylistFactory(owner=self.owner),
            resource_file=None,
            resource=self.annotation_set.resource,
            annotation_set=self.annotation_set,
        )
        self.track = TrackFactory(annotation_set=self.annotation_set)
        self.url = reverse("delete_annotation_set", args=[self.annotation_set.pk])
        self.client.force_login(self.owner)
        session = self.client.session
        session["oidc_id_token_expiration"] = time.time() + 3600
        session.save()

    def _shared_content(self, owner):
        return ContentFactory(
            playlist=PlaylistFactory(owner=owner),
            resource_file=None,
            resource=self.annotation_set.resource,
            annotation_set=self.annotation_set,
        )

    def _assert_unchanged(self, *shared_contents):
        updated_at = self.annotation_set.updated_at
        self.annotation_set.refresh_from_db()
        self.assertEqual(self.annotation_set.owner_id, self.owner.pk)
        self.assertEqual(self.annotation_set.updated_at, updated_at)
        self.track.refresh_from_db()
        self.assertEqual(self.track.annotation_set_id, self.annotation_set.pk)
        for content in (self.content, *shared_contents):
            content.refresh_from_db()
            self.assertEqual(content.annotation_set_id, self.annotation_set.pk)

    def _assert_transferred(self, new_owner, *shared_contents):
        self.annotation_set.refresh_from_db()
        self.assertEqual(self.annotation_set.owner_id, new_owner.pk)
        self.content.refresh_from_db()
        self.assertIsNone(self.content.annotation_set_id)
        self.track.refresh_from_db()
        self.assertEqual(self.track.annotation_set_id, self.annotation_set.pk)
        for content in shared_contents:
            content.refresh_from_db()
            self.assertEqual(content.annotation_set_id, self.annotation_set.pk)

    def _render_owner_select(self):
        html = render_to_string(
            "core/partials/annotation_set_retirement_modal.html",
            {
                "annotation_set": self.annotation_set,
                "annotation_set_owner_candidates": (
                    self.annotation_set.get_ownership_candidates()
                ),
            },
        )
        parser = _OwnerSelectParser()
        parser.feed(html)
        return parser

    def test_deletes_a_set_without_another_owner_using_it(self):
        response = self.client.delete(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            AnnotationSet.objects.filter(pk=self.annotation_set.pk).exists()
        )
        self.assertFalse(Track.objects.filter(pk=self.track.pk).exists())
        self.content.refresh_from_db()
        self.assertIsNone(self.content.annotation_set_id)

    def test_transfers_a_shared_set_and_detaches_the_departing_owners_content(self):
        other_owner = UserFactory(instructor=True)
        other_content = ContentFactory(
            playlist=PlaylistFactory(owner=other_owner),
            resource_file=None,
            resource=self.annotation_set.resource,
            annotation_set=self.annotation_set,
        )

        response = self.client.delete(self.url)

        self.assertEqual(response.status_code, 200)
        self.annotation_set.refresh_from_db()
        self.assertEqual(self.annotation_set.owner_id, other_owner.pk)
        self.content.refresh_from_db()
        self.assertIsNone(self.content.annotation_set_id)
        other_content.refresh_from_db()
        self.assertEqual(other_content.annotation_set_id, self.annotation_set.pk)
        self.assertTrue(Track.objects.filter(pk=self.track.pk).exists())

    def _assert_selected_owner_transfer(self, string_id=False):
        default_owner = UserFactory(instructor=True)
        selected_owner = UserFactory(instructor=True)
        default_content = self._shared_content(default_owner)
        selected_content = self._shared_content(selected_owner)
        self.assertEqual(
            list(self.annotation_set.get_ownership_candidates()),
            [default_owner, selected_owner],
        )
        owner_id = str(selected_owner.pk) if string_id else selected_owner.pk

        with patch.object(
            AnnotationSet,
            "delete_or_reassign_ownership",
            autospec=True,
            side_effect=AnnotationSet.delete_or_reassign_ownership,
        ) as remove:
            response = self.client.delete(
                self.url,
                data=json.dumps({"new_owner_id": owner_id}),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        remove.assert_called_once_with(
            self.annotation_set, self.owner, new_owner=selected_owner
        )
        self._assert_transferred(selected_owner, default_content, selected_content)

    def test_selecting_a_non_default_eligible_owner(self):
        self._assert_selected_owner_transfer()

    def test_selected_owner_id_can_be_a_string(self):
        self._assert_selected_owner_transfer(string_id=True)

    def test_null_owner_id_uses_automatic_fallback(self):
        admin = UserFactory(admin=True)
        other_owner = UserFactory(instructor=True)
        admin_content = self._shared_content(admin)
        other_content = self._shared_content(other_owner)

        with patch.object(
            AnnotationSet,
            "delete_or_reassign_ownership",
            autospec=True,
            side_effect=AnnotationSet.delete_or_reassign_ownership,
        ) as remove:
            response = self.client.delete(
                self.url,
                data=json.dumps({"new_owner_id": None}),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        remove.assert_called_once_with(self.annotation_set, self.owner, new_owner=None)
        self._assert_transferred(other_owner, admin_content, other_content)

    def test_invalid_owner_ids_are_rejected_without_mutation(self):
        eligible_owner = UserFactory(instructor=True)
        shared_content = self._shared_content(eligible_owner)
        ineligible_owner = UserFactory(instructor=True)
        self._shared_content(ineligible_owner).delete()
        missing_owner = UserFactory(instructor=True)
        missing_owner_id = missing_owner.pk
        missing_owner.delete()

        for owner_id in (
            self.owner.pk,
            ineligible_owner.pk,
            missing_owner_id,
            "not-a-user-id",
            True,
            False,
            1.5,
            [],
            {},
        ):
            with self.subTest(new_owner_id=owner_id):
                response = self.client.delete(
                    self.url,
                    data=json.dumps({"new_owner_id": owner_id}),
                    content_type="application/json",
                )

                self.assertEqual(response.status_code, 400)
                self.assertEqual(list(get_messages(response.wsgi_request)), [])
                self._assert_unchanged(shared_content)

    def test_malformed_or_non_object_json_is_rejected_without_mutation(self):
        shared_content = self._shared_content(UserFactory(instructor=True))

        for body in ('{"new_owner_id":', "{", "[]", '"owner"', "42", "true"):
            with self.subTest(body=body):
                response = self.client.delete(
                    self.url, data=body, content_type="application/json"
                )

                self.assertEqual(response.status_code, 400)
                self.assertEqual(list(get_messages(response.wsgi_request)), [])
                self._assert_unchanged(shared_content)

    def test_method_rejects_explicit_invalid_owner_without_mutation(self):
        ineligible_owner = UserFactory(instructor=True)
        shared_contents = []
        for has_other_owner in (False, True):
            if has_other_owner:
                shared_contents.append(
                    self._shared_content(UserFactory(instructor=True))
                )
            for invalid_owner in (self.owner, ineligible_owner):
                with self.subTest(
                    has_other_owner=has_other_owner, new_owner_id=invalid_owner.pk
                ):
                    self.assertFalse(
                        self.annotation_set.delete_or_reassign_ownership(
                            self.owner, new_owner=invalid_owner
                        )
                    )
                    self.assertEqual(self.annotation_set.owner_id, self.owner.pk)
                    self._assert_unchanged(*shared_contents)

    def test_non_owner_admin_cannot_delete_or_reassign_the_set(self):
        admin = UserFactory(admin=True)
        shared_content = self._shared_content(admin)
        self.client.force_login(admin)
        session = self.client.session
        session["oidc_id_token_expiration"] = time.time() + 3600
        session.save()

        for body in ("", json.dumps({"new_owner_id": admin.pk})):
            with self.subTest(body=body):
                with patch.object(
                    AnnotationSet, "delete_or_reassign_ownership"
                ) as remove:
                    response = self.client.delete(
                        self.url, data=body, content_type="application/json"
                    )

                self.assertEqual(response.status_code, 403)
                remove.assert_not_called()
                self._assert_unchanged(shared_content)

    def test_candidates_are_distinct_exclude_owner_and_order_non_admins_first(self):
        admin = UserFactory(admin=True)
        first_owner = UserFactory(instructor=True)
        second_owner = UserFactory(instructor=True)
        self._shared_content(second_owner)
        self._shared_content(admin)
        first_content = self._shared_content(first_owner)
        self._shared_content(first_owner)
        ContentFactory(
            playlist=first_content.playlist,
            resource_file=None,
            resource=self.annotation_set.resource,
            annotation_set=None,
        )

        response = self.client.get(reverse("video_editor", args=[self.content.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(response.context["annotation_set_owner_candidates"]),
            [first_owner, second_owner, admin],
        )

    def test_false_result_returns_server_error_without_a_success_message(self):
        with patch.object(
            AnnotationSet, "delete_or_reassign_ownership", return_value=False
        ) as remove:
            response = self.client.delete(self.url)

        remove.assert_called_once_with(self.owner, new_owner=None)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(list(get_messages(response.wsgi_request)), [])
        self.annotation_set.refresh_from_db()
        self.assertEqual(self.annotation_set.owner_id, self.owner.pk)
        self.content.refresh_from_db()
        self.assertEqual(self.content.annotation_set_id, self.annotation_set.pk)

    def test_unexpected_exception_returns_server_error_without_a_success_message(self):
        with (
            patch.object(
                AnnotationSet,
                "delete_or_reassign_ownership",
                side_effect=RuntimeError("Removal failed"),
            ),
            self.assertLogs("core.views_video_editor", level="ERROR"),
        ):
            response = self.client.delete(self.url)

        self.assertEqual(response.status_code, 500)
        self.assertEqual(list(get_messages(response.wsgi_request)), [])
