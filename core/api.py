# This file defines the functions used to request data from OIT's APIs

import requests
from datetime import datetime, timedelta
import yvideo.secret_settings as secret_settings
from .models import AuthToken


class Api:
    def __init__(self):
        auth_tokens = AuthToken.objects.all()
        auth_tokens_count = len(list(auth_tokens))

        # don't allow auth token to be older than 1 hour
        oldest_valid_time = datetime.now() - timedelta(hours=1)

        # filter for tokens that have a creation date greater than (__gt) the oldest valid time
        valid_auth_tokens = AuthToken.objects.filter(created_at__gt=oldest_valid_time)
        valid_auth_tokens_count = len(list(valid_auth_tokens))

        if auth_tokens_count == 1 and valid_auth_tokens_count == 1:
            # there is only one token, and it is valid
            self.auth_token = auth_tokens.first().token
        else:
            # there are either more than 1 token, or that token is invalid
            # either way, delete everything and generate a new token
            auth_tokens.delete()
            auth_token = self.generate_auth_token()
            self.auth_token = AuthToken.objects.create(token=auth_token).token

    def generate_auth_token(self):
        # request an auth token from OIT's auth token granting endpoint
        token_request = requests.post(
            "https://api.byu.edu/token",
            data={"grant_type": "client_credentials"},
            auth=(secret_settings.CLIENT_ID, secret_settings.CLIENT_SECRET),
        )
        token_json_result = token_request.json()
        return token_json_result["access_token"]

    def build_auth_header(self):
        auth_header = f"Bearer {self.auth_token}"
        return auth_header

    def get_current_year_term(self):
        # to determine current year term, we have to compare to today's date
        today_datetime = datetime.today().strftime("%Y-%m-%dT%H:%M:%S")

        # get yearterm information
        url = "https://api.byu.edu/bdp/student_academics/academic_control_dates/v1/?control_date_type=CURRICULUM"
        headers = {"Authorization": self.build_auth_header()}
        control_date_request = requests.get(url, headers=headers)
        control_date_json_response = control_date_request.json()
        response_data = control_date_json_response["data"]

        # determine which yearterm corresponds to current datetime
        yearterm = None
        for entry in response_data:
            if (
                entry["start_date_time"] <= today_datetime
                and entry["end_date_time"] > today_datetime
            ):
                yearterm = entry["year_term"]
                break

        return yearterm
