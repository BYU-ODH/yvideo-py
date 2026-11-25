from ..models import Resource

LEGACY_URL_ONLY_RESOURCE_ID = "00000000-0000-0000-0000-000000000000"


def map_legacy_media_type(legacy_value):
    value = (legacy_value or "").strip().lower()
    if value in {"vid", "video"}:
        return Resource.MediaType.VIDEO
    if value in {"aud", "audio"}:
        return Resource.MediaType.AUDIO
    if value in {"txt", "text"}:
        return Resource.MediaType.TEXT
    if value in {"www", "web", "online-media"}:
        return Resource.MediaType.WEB
    return Resource.MediaType.VIDEO
