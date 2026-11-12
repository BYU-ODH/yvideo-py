from django import forms

from .models import ICProgram
from .models import ICProgramItem


class ICProgramForm(forms.ModelForm):
    class Meta:
        model = ICProgram
        fields = ["name", "start_date", "end_date", "notes"]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_date"), cleaned.get("end_date")
        if start and end and end < start:
            self.add_error("end_date", "The program can't end before it starts.")
        return cleaned


class ICProgramItemForm(forms.ModelForm):
    class Meta:
        model = ICProgramItem
        fields = ["mode", "auto_advance", "subtitles", "timing_verified"]
