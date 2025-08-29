from django import forms
from django.core.exceptions import ValidationError

from .models import Collection
from .models import Content


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


class UpdateContentForm(forms.ModelForm):
    def clean_title(self):
        title = self.cleaned_data["title"]
        collection = self.instance.collection if self.instance else None

        if collection:
            query = Content.objects.filter(collection=collection, title=title)
            if self.instance and self.instance.pk:
                query = query.exclude(pk=self.instance.pk)

            if query.exists():
                raise ValidationError(
                    "A content item with this title already exists in this collection."
                )

        return title

    class Meta:
        model = Content
        fields = (
            "title",
            "description",
            "tags",
            "allow_definitions",
            "allow_notes",
            "allow_captions",
            "published",
            "words",
        )
