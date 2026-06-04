from django import forms
from django.core.exceptions import ValidationError

from .models import Clip
from .models import Collection
from .models import Content
from .models import ImportantWord
from .models import ResourceContentIntakeRequest
from .models import Subtitle
from .utils import hms2seconds


class CollectionForm(forms.ModelForm):
    name = forms.CharField(
        widget=forms.TextInput(attrs={"placeholder": "Collection Name"})
    )

    def clean_name(self):
        name = self.cleaned_data["name"]

        if Collection.objects.filter(
            owner=self.initial.get("user"), name=name
        ).exists():
            raise ValidationError("You already have a collection with this name.")

        return name

    class Meta:
        model = Collection
        fields = ("name",)


class CollectionSettingsForm(forms.ModelForm):
    class Meta:
        model = Collection
        fields = ["id", "name", "published", "archived"]

    id = forms.CharField(widget=forms.HiddenInput)


class ContentForm(forms.ModelForm):
    confirm_guidelines = forms.BooleanField(label="guidelines", required=True)

    class Meta:
        model = Content
        fields = [
            "title",
            "description",
            "allow_definitions",
            "allow_notes",
            "allow_captions",
            "resource_file",
        ]


class ContentSettingsForm(forms.ModelForm):
    class Meta:
        model = Content
        fields = [
            "title",
            "description",
            "allow_definitions",
            "allow_notes",
            "allow_captions",
            "published",
            "words",
        ]


class UpdateContentForm(forms.ModelForm):
    confirm_guidelines = forms.BooleanField(label="guidelines", required=True)

    class Meta:
        model = Content
        fields = [
            "id",
            "title",
            "description",
            "allow_definitions",
            "allow_notes",
            "allow_captions",
            "published",
        ]

    id = forms.CharField(widget=forms.HiddenInput)


class ImportantWordForm(forms.ModelForm):
    class Meta:
        model = ImportantWord
        fields = ["word", "translation"]

    word = forms.CharField(required=True)
    translation = forms.CharField(required=True)


class ClipForm(forms.ModelForm):
    class Meta:
        model = Clip
        fields = ["name", "start_time", "end_time"]

    def clean(self):
        cleaned_data = super().clean()
        start_time = cleaned_data.get("start_time")
        end_time = cleaned_data.get("end_time")

        if start_time and end_time:
            start_seconds = hms2seconds(start_time)
            end_seconds = hms2seconds(end_time)

            if start_seconds >= end_seconds:
                raise forms.ValidationError("End time must be after start time.")

        return cleaned_data


class SubtitleForm(forms.ModelForm):
    class Meta:
        model = Subtitle
        fields = [
            "resource",
            "owner",
            "language",
            "name",
            "subtitles_file",
            "is_original",
        ]

    resource = forms.CharField(widget=forms.HiddenInput)
    owner = forms.CharField(widget=forms.HiddenInput)


class ResourceContentIntakeRequestForm(forms.ModelForm):
    class Meta:
        model = ResourceContentIntakeRequest
        exclude = []
