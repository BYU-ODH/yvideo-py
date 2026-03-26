"""Standard local test settings for the pre-pilot development phase.

These settings intentionally tell Django to build the `core` test schema directly
from the current models instead of treating migrations as authoritative. That fits
the current workflow: model design is still moving, and migrations are not being
committed yet.

TODO: Once the schema is considered stable for the pilot and `core` migrations
become the committed source of truth, remove the `MIGRATION_MODULES` override and
switch local/pre-commit tests back to the normal project settings so test database
creation exercises the real migration history.
"""

from .settings import *

MIGRATION_MODULES = {"core": None}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
DEBUG = False

if "debug_toolbar" in INSTALLED_APPS:
    INSTALLED_APPS = [app for app in INSTALLED_APPS if app != "debug_toolbar"]

MIDDLEWARE = [
    middleware
    for middleware in MIDDLEWARE
    if middleware != "debug_toolbar.middleware.DebugToolbarMiddleware"
]
