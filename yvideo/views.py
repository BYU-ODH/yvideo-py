from django.conf import settings
from django.http import HttpResponse
from django.http import HttpResponseRedirect
from django.http import HttpResponseServerError
from django.views.decorators.csrf import csrf_exempt
from onelogin.saml2.auth import OneLogin_Saml2_Auth
from onelogin.saml2.settings import OneLogin_Saml2_Settings
from onelogin.saml2.utils import OneLogin_Saml2_Utils

from core import model_utils as core_model_utils


def init_saml_auth(req):
    auth = OneLogin_Saml2_Auth(req, custom_base_path=settings.SAML_FOLDER)
    return auth


def prepare_django_request(request):
    # If server is behind proxys or balancers use the HTTP_X_FORWARDED fields
    result = {
        "https": "on",
        "http_host": request.META["HTTP_HOST"],
        "script_name": request.META["PATH_INFO"],
        "get_data": request.GET.copy(),
        # Uncomment if using ADFS as IdP, https://github.com/onelogin/python-saml/pull/144
        # 'lowercase_urlencoding': True,
        "post_data": request.POST.copy(),
    }
    return result


@csrf_exempt
def index(request):
    req = prepare_django_request(request)
    auth = init_saml_auth(req)
    errors = []

    if "sso" in req["get_data"]:
        # return HttpResponseRedirect(auth.login())
        # If AuthNRequest ID need to be stored in order to later validate it, do instead
        sso_built_url = auth.login()
        request.session["AuthNRequestID"] = auth.get_last_request_id()
        return HttpResponseRedirect(sso_built_url)

    elif "acs" in req["get_data"]:
        request_id = None
        if "AuthNRequestID" in request.session:
            request_id = request.session["AuthNRequestID"]

        if request_id is None:
            return HttpResponseRedirect("/?sso")

        auth.process_response(request_id=request_id)
        errors = auth.get_errors()

        if not errors:
            if "AuthNRequestID" in request.session:
                del request.session["AuthNRequestID"]
            request.session["samlUserdata"] = auth.get_attributes()
            request.session["samlNameId"] = auth.get_nameid()
            request.session["samlNameIdFormat"] = auth.get_nameid_format()
            request.session["samlNameIdNameQualifier"] = auth.get_nameid_nq()
            request.session["samlNameIdSPNameQualifier"] = auth.get_nameid_spnq()
            request.session["samlSessionIndex"] = auth.get_session_index()
            byuId = request.session["samlUserdata"]["byuId"]
            user_result = core_model_utils.create_or_update_user(byuId)
            if user_result["user"] is not None:
                user = user_result["user"]
                request.session["netid"] = user["netid"]
                request.session["byuid"] = user["byu_id"]
            else:
                request.session["netid"] = None
                request.session["byuid"] = None

            if (
                "RelayState" in req["post_data"]
                and OneLogin_Saml2_Utils.get_self_url(req)
                != req["post_data"]["RelayState"]
            ):
                # To avoid 'Open Redirect' attacks, before execute the redirection confirm
                # the value of the req['post_data']['RelayState'] is a trusted URL.
                relay_state = "https://yvideo.byu.edu/"
                return HttpResponseRedirect(relay_state)


def metadata(request):
    # req = prepare_django_request(request)
    # auth = init_saml_auth(req)
    # saml_settings = auth.get_settings()
    saml_settings = OneLogin_Saml2_Settings(
        settings=None, custom_base_path=settings.SAML_FOLDER, sp_validation_only=True
    )
    metadata = saml_settings.get_sp_metadata()
    errors = saml_settings.validate_metadata(metadata)

    if len(errors) == 0:
        resp = HttpResponse(content=metadata, content_type="text/xml")
    else:
        resp = HttpResponseServerError(content=", ".join(errors))
    return resp
