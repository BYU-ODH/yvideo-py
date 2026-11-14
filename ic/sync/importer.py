"""Replace the booth's mirror with production's rows, verifying before committing."""

from pathlib import Path

from django.core import serializers
from django.db import connection
from django.db import transaction

from core.models import Content
from core.models import Subtitle

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


def _subtitle_paths():
    return {Path(s.subtitles_file.path) for s in Subtitle.objects.all()}


def _write_subtitles(texts, previous):
    """Write each mirrored subtitle's text at its row's path, first recording the path's
    previous bytes (or None) in `previous`, so a failed import can put them back.

    A None text (production couldn't read the file) leaves no file, so the booth renders
    "" exactly as production does.
    """
    for subtitle in Subtitle.objects.all():
        path = Path(subtitle.subtitles_file.path)
        previous[path] = path.read_bytes() if path.exists() else None
        text = texts.get(str(subtitle.pk))
        if text is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text.encode("utf-8"))


def _restore(previous):
    for path, data in previous.items():
        if data is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(data)


def apply_fixture(rows, fingerprints, subtitle_texts):
    """Load the fixture and subtitles, and roll both back unless every fingerprint matches.

    Deserialized saves run with raw=True, so model save() overrides (checksums, barcode
    generation, geometry clamping) do not touch production's values on the way in.
    """
    old_paths = _subtitle_paths()
    previous = {}
    try:
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
            # The player JSON embeds subtitle text, so it must be in place before the
            # fingerprints are checked.
            _write_subtitles(subtitle_texts, previous)

            mismatched = []
            for content_id, expected in sorted(
                fingerprints.items(), key=lambda kv: int(kv[0])
            ):
                content = Content.objects.filter(pk=int(content_id)).first()
                if content is None or content_fingerprint(content) != expected:
                    mismatched.append(int(content_id))
            if mismatched:
                raise FingerprintMismatch(mismatched)
    except BaseException:
        _restore(previous)
        raise
    # Subtitles production no longer has.
    for path in old_paths - set(previous):
        path.unlink(missing_ok=True)
