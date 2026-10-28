from enum import IntEnum

from ..models import PlaylistRole


class LegacyCollectionRole(IntEnum):
    """`collection_access.account_role` values in the legacy y-video database."""

    INSTRUCTOR = 0
    TA = 1
    STUDENT = 2
    AUDITOR = 3


# Legacy instructors and TAs had identical access, as did students and auditors.
LEGACY_COLLECTION_ROLE_TO_PLAYLIST_ROLE = {
    LegacyCollectionRole.INSTRUCTOR: PlaylistRole.COLLABORATOR,
    LegacyCollectionRole.TA: PlaylistRole.COLLABORATOR,
    LegacyCollectionRole.STUDENT: PlaylistRole.STUDENT,
    LegacyCollectionRole.AUDITOR: PlaylistRole.STUDENT,
}


def map_legacy_collection_role(legacy_value):
    """Map a legacy `collection_access.account_role` to a PlaylistRole, or None.

    None is load-bearing: preflight files an unknown_collection_role warning for it and
    the import skips the grant entirely.
    """
    try:
        return LEGACY_COLLECTION_ROLE_TO_PLAYLIST_ROLE.get(
            LegacyCollectionRole(int(legacy_value))
        )
    except (TypeError, ValueError):
        return None
