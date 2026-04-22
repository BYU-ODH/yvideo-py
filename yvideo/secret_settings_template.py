# Copy this file to secret_settings.py
# NEVER COMMIT secret_settings.py to the repository!
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver"]
DEBUG = True
DEV_QUICK_LOGIN_ENABLED = False
# Development-only fallback so local runs and CI tests work without secret_settings.py.
SECRET_KEY = "dev-only-insecure-secret-key"
TIME_ZONE = "America/Denver"
API_CLIENT_ID = ""
API_CLIENT_SECRET = ""
# For URLs that contain query string variables that differ based on logged in user,
# provide the entire url up to the '?', exclusive.
# example: api.example.com/v1/?some_variable=some_value should be recorded here as:
# API_EXAMPLE = "api.example.com/v1/"
# a method using this url will append the ?some_variable=some_value to the end of the url
API_AUTH_TOKEN_URL = ""
API_YEARTERM_URL = ""
API_WORKER_ID_IAM_URL = ""
API_WORKER_SUMMARY_URL = ""
API_STUDENT_SUMMARY_URL = ""
API_STUDENT_ENROLLMENTS_URL = ""


# Required when behind a reverse proxy (Apache, nginx).
# Uncomment both lines for deployed environments.
# WARNING Modifying this setting can compromise your site’s security. Ensure you fully understand your setup before changing it.
# Make sure ALL of the following are true before setting this (assuming the values from the example above):
# * Your Django app is behind a proxy.
# * Your proxy strips the X-Forwarded-Proto header from all incoming requests, even when it contains a comma-separated list of protocols. In other words, if end users include that header in their requests, the proxy will discard it.
# * Your proxy sets the X-Forwarded-Proto header and sends it to Django, but only for requests that originally come in via HTTPS.
# If any of those are not true, you should keep this setting set to its default None and find another way of determining HTTPS, perhaps via custom middleware.
# SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# CSRF_TRUSTED_ORIGINS = ["https://example.com"]
