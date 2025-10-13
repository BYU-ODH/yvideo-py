from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth import login
from django.contrib.auth.decorators import login_not_required
from django.http import HttpResponse
from django.http import HttpResponseRedirect
from django.http import HttpResponseServerError
from django.views.decorators.csrf import csrf_exempt
from onelogin.saml2.auth import OneLogin_Saml2_Auth
from onelogin.saml2.errors import OneLogin_Saml2_Error
from onelogin.saml2.settings import OneLogin_Saml2_Settings

from core import model_utils as core_model_utils

login = login_not_required(login)


@login_not_required
def init_saml_auth(req):
    auth = OneLogin_Saml2_Auth(req, custom_base_path=settings.SAML_FOLDER)
    return auth


@login_not_required
def prepare_django_request(request):
    # If server is behind proxys or balancers use the HTTP_X_FORWARDED fields
    result = {
        "https": "on",
        "http_host": request.META["HTTP_HOST"],
        "script_name": request.META["PATH_INFO"],
        "get_data": request.GET.copy(),
        # Uncomment if using ADFS (Active Directory Federated Service) as IdP, https://github.com/onelogin/python-saml/pull/144
        # 'lowercase_urlencoding': True,
        "post_data": request.POST.copy(),
    }
    return result


@csrf_exempt
@login_not_required
def saml_login(request):
    req = prepare_django_request(request)
    auth = init_saml_auth(req)
    errors = []

    if "sso" in req["get_data"]:
        # return HttpResponseRedirect(auth.login())
        # If AuthNRequest ID need to be stored in order to later validate it, do instead
        sso_built_url = auth.login()
        request.session["AuthNRequestID"] = auth.get_last_request_id()
        # record the desired destination in session so we don't lose it going back and forth
        # from the SP and IdP
        request.session["requested_endpoint"] = (
            req["get_data"]["next"] if "next" in req["get_data"] else "/"
        )
        return HttpResponseRedirect(sso_built_url)

    elif "acs" in req["get_data"]:
        request_id = None
        if "AuthNRequestID" in request.session:
            request_id = request.session["AuthNRequestID"]

        if request_id is None:
            return HttpResponseRedirect("?sso")

        # if the login wasn't successful, or is outdated, a OneLogin_Saml2_Error occurs,
        # redirect user to CAS so they can renew their SSO credentials
        try:
            auth.process_response(request_id=request_id)
        except OneLogin_Saml2_Error:
            return HttpResponseRedirect("?sso")

        errors = auth.get_errors()
        is_saml_authenticated = auth.is_authenticated()

        if not errors and is_saml_authenticated:
            if "AuthNRequestID" in request.session:
                del request.session["AuthNRequestID"]
            request.session["samlUserdata"] = auth.get_attributes()
            request.session["samlNameId"] = auth.get_nameid()
            request.session["samlNameIdFormat"] = auth.get_nameid_format()
            request.session["samlNameIdNameQualifier"] = auth.get_nameid_nq()
            request.session["samlNameIdSPNameQualifier"] = auth.get_nameid_spnq()
            request.session["samlSessionIndex"] = auth.get_session_index()
            byuId = request.session["samlUserdata"]["byuId"]
            if isinstance(byuId, list) and byuId:
                byuId = byuId[0]
            user_result = core_model_utils.create_or_update_user(byuId)
            auth_user = authenticate(request, byu_id=byuId)
            host = settings.ALLOWED_HOSTS[0]
            protocol = "https://"
            if host == "localhost" or host == "127.0.0.1":
                protocol = "http://"
            root_redirect_url = protocol + host
            if auth_user is not None:
                login(
                    request=request,
                    user=auth_user,
                    backend="yvideo.customAuth.CustomAuth",
                )
                # this "user" attribute of user_result is a dict, not a user object. see create_or_update_user
                request.session["user"] = user_result["user"]
                redirect_url = root_redirect_url + request.session["requested_endpoint"]
                del request.session["requested_endpoint"]
                return HttpResponseRedirect(auth.redirect_to(redirect_url))
            else:
                request.session["user"] = None
                return HttpResponseRedirect(
                    auth.redirect_to(root_redirect_url + "/invalid-login")
                )
        else:
            return HttpResponseRedirect("?sso")


@login_not_required
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
