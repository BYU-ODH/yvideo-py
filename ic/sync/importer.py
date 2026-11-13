"""Replace the booth's mirror with production's rows, verifying before committing."""

from django.core import serializers
from django.db import connection
from django.db import transaction

from core.models import Content

from .bundle import MIRRORED_MODELS
from .bundle import content_fingerprint


class FingerprintMismatch(Exception):
    def __init__(self, content_ids):
        self.content_ids = content_ids
        super().__init__(
            "Player data differs from production for content "
            + ", ".join(str(c) for c in content_ids)
            + "; the booth kept its previous data."
        )


def apply_fixture(rows, fingerprints):
    """Load the fixture in one transaction and roll back unless every fingerprint matches.

    Deserialized saves run with raw=True, so model save() overrides (checksums, barcode
    generation, geometry clamping) do not touch production's values on the way in.
    """
    with transaction.atomic():
        with connection.constraint_checks_disabled():
            # Delete every mirrored row, children first.
            for model in reversed(MIRRORED_MODELS):
                model._base_manager.all().delete()
            for deserialized in serializers.deserialize("python", rows):
                deserialized.save()
        connection.check_constraints(
            table_names=[m._meta.db_table for m in MIRRORED_MODELS]
        )

        mismatched = []
        for content_id, expected in sorted(
            fingerprints.items(), key=lambda kv: int(kv[0])
        ):
            content = Content.objects.filter(pk=int(content_id)).first()
            if content is None or content_fingerprint(content) != expected:
                mismatched.append(int(content_id))
        if mismatched:
            raise FingerprintMismatch(mismatched)
