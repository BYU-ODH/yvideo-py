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

    The bulk import refuses to guess a role for None and stops with an error.
    """
    try:
        return LEGACY_COLLECTION_ROLE_TO_PLAYLIST_ROLE.get(
            LegacyCollectionRole(int(legacy_value))
        )
    except (TypeError, ValueError):
        return None
