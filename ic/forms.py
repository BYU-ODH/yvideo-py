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


class ICProgramItemForm(forms.ModelForm):
    class Meta:
        model = ICProgramItem
        fields = ["mode", "auto_advance", "subtitles", "timing_verified"]
