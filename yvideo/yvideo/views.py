from django.http import HttpResponse


def index(request):
    return HttpResponse(
        "Hello, world! This is the YVideo app index page.", content_type="text/plain"
    )
