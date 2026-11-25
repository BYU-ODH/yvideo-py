from .models import LegacySourceMap

# Imported users get this username until their first login, when it is
# replaced by their BYU ID. A BYU ID is all digits, so it never has this
# prefix, and Django admin accepts the hyphen when saving the user.
LEGACY_USERNAME_PREFIX = "legacy-"

__all__ = ["LEGACY_USERNAME_PREFIX", "LegacySourceMap"]
