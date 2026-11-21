from .models import LegacySourceMap

# Imported users get this username until their first login, when it is
# replaced by their BYU ID. A BYU ID never contains a colon.
LEGACY_USERNAME_PREFIX = "legacy:"

__all__ = ["LEGACY_USERNAME_PREFIX", "LegacySourceMap"]
