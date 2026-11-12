from django.http import HttpResponseNotAllowed

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# The only write a booth accepts: the operator's "Sync now". It can only queue an
# ICSyncRun; the run itself fetches from production and verifies everything.
SYNC_START_VIEWS = {"ic_sync_run_create"}


class ICReadOnlyMiddleware:
    """Safety net behind the booth urlconf: no route may change mirrored data.

    The urlconf already contains only booth routes; this refuses any unsafe method that
    reaches a view other than the sync-start allowlist, in case one is ever added.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        if request.method in SAFE_METHODS:
            return None
        match = request.resolver_match
        if match is not None and match.url_name in SYNC_START_VIEWS:
            return None
        return HttpResponseNotAllowed(sorted(SAFE_METHODS))
