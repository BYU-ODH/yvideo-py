# Copy this file to secret_settings.py
# NEVER COMMIT secret_settings.py to the repository!
ALLOWED_HOSTS = []
DEBUG = True
SECRET_KEY = ""
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
