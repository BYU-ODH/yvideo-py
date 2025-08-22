from django import forms

from .models import Collection


class CollectionForm(forms.ModelForm):
    name = forms.CharField(
        widget=forms.TextInput(attrs={"placeholder": "Collection Name"})
    )

    class Meta:
        model = Collection
        fields = ("name",)
