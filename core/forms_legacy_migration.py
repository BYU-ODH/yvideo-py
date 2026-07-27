import logging

from django import forms

from .legacy_migration import LegacyMigrationKind
from .legacy_migration import LegacyMigrationRequest

logger = logging.getLogger(__name__)


class LegacyMigrationRequestForm(forms.ModelForm):
    migration_kind = forms.ChoiceField(
        choices=LegacyMigrationKind.choices,
        label="What would you like to move?",
    )
    legacy_reference = forms.CharField(
        label="Old Y-Video link or ID",
        help_text=(
            "Paste the link to the collection or video in the old Y-Video. "
            "You can also enter its ID."
        ),
        widget=forms.TextInput(
            attrs={
                "placeholder": "Paste the old Y-Video link or ID",
            }
        ),
    )

    class Meta:
        model = LegacyMigrationRequest
        fields = [
            "migration_kind",
            "legacy_reference",
            "request_notes",
        ]
        labels = {
            "request_notes": "Anything else we should know?",
        }
        help_texts = {
            "request_notes": "Optional: add any details that may help with your request.",
        }
        widgets = {
            "request_notes": forms.Textarea(attrs={"rows": 4}),
        }
