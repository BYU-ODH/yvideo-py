from io import StringIO
import os

from django.core.management import call_command
import pytest

# pytest-playwright initializes an event loop before Django's test DB setup.
# These browser tests intentionally use Django's sync ORM and live server.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


@pytest.fixture
def seeded_demo_data(settings, tmp_path, transactional_db):
    media_root = tmp_path / "media"
    media_root.mkdir()

    settings.DEBUG = True
    settings.DEV_QUICK_LOGIN_ENABLED = True
    settings.MEDIA_ROOT = media_root
    settings.ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver"]
    settings.SECRET_KEY = "test-secret-key"

    call_command("seed_demo_data", stdout=StringIO())

    return media_root
