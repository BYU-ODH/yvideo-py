from django.shortcuts import render
from .models import User


def index(request):
    context = {
        "user": User.objects.first(),  # TODO: Replace with actual data
        "collections": [],
        "public_collections": [],
    }
    return render(request, "index.html", context)
