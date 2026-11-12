"""Replace the booth's mirror with production's rows, verifying before committing."""

import json

from django.core import serializers
from django.db import connection
from django.db import transaction

from core.models import Content

from .bundle import content_fingerprint
from .bundle import mirrored_model_classes


class FingerprintMismatch(Exception):
    def __init__(self, content_ids):
        self.content_ids = content_ids
        super().__init__(
            "Player data differs from production for content "
            + ", ".join(str(c) for c in content_ids)
            + "; the booth kept its previous data."
        )


def clear_mirror():
    """Delete every mirrored row, children first."""
    for model in reversed(mirrored_model_classes()):
        model._base_manager.all().delete()


def apply_fixture(fixture_bytes, fingerprints):
    """Load the fixture in one transaction and roll back unless every fingerprint matches.

    Deserialized saves run with raw=True, so model save() overrides (checksums, barcode
    generation, geometry clamping) do not touch production's values on the way in.
    """
    models = mirrored_model_classes()
    with transaction.atomic():
        with connection.constraint_checks_disabled():
            clear_mirror()
            for deserialized in serializers.deserialize("json", fixture_bytes):
                deserialized.save()
        connection.check_constraints(table_names=[m._meta.db_table for m in models])

        mismatched = []
        for content_id, expected in sorted(
            fingerprints.items(), key=lambda kv: int(kv[0])
        ):
            content = Content.objects.filter(pk=int(content_id)).first()
            if content is None or content_fingerprint(content) != expected:
                mismatched.append(int(content_id))
        if mismatched:
            raise FingerprintMismatch(mismatched)
    return len(json.loads(fixture_bytes))
