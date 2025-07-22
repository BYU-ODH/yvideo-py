# This file defines the functions used to request data from OIT's APIs

from datetime import datetime
from datetime import timedelta

import requests

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
            secret_settings.API_AUTH_TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(secret_settings.API_CLIENT_ID, secret_settings.API_CLIENT_SECRET),
        )
        token_json_result = token_request.json()
        return token_json_result["access_token"]

    def build_auth_header(self):
        auth_header = f"Bearer {self.auth_token}"
        return auth_header

    def calculate_next_year_term(self, yearterm_string):
        year_string = yearterm_string[:4]
        term_string = yearterm_string[4:]
        new_year_string = None
        new_term_string = None
        # Fall to Winter
        if term_string == "5":
            new_year_string = str(int(year_string) + 1)
            new_term_string = "1"
        # Winter to Spring
        elif term_string == "1":
            new_year_string = year_string
            new_term_string = "3"
        # Spring to Summer
        elif term_string == "3":
            new_year_string = year_string
            new_term_string = "4"
        # Summer to Fall
        elif term_string == "4":
            new_year_string = year_string
            new_term_string = "5"

        return new_year_string + new_term_string

    def get_current_year_term(self):
        # to determine current year term, we have to compare to today's date
        today_datetime = datetime.today().strftime("%Y-%m-%dT%H:%M:%S")

        # get yearterm information
        url = secret_settings.API_YEARTERM_URL
        headers = {"Authorization": self.build_auth_header()}
        control_date_request = requests.get(url, headers=headers)
        control_date_json_response = control_date_request.json()
        response_data = control_date_json_response["data"]

        # determine which yearterm corresponds to current datetime
        yearterm = None
        is_two_weeks_from_end = False
        for entry in response_data:
            if (
                entry["start_date_time"] <= today_datetime
                and entry["end_date_time"] > today_datetime
            ):
                yearterm = entry["year_term"]
                yearterm_end_datetime = datetime.strptime(
                    entry["end_date_time"], "%Y-%m-%dT%H:%M:%S"
                )
                # determine if the end of the current year term is 2 weeks or less away
                two_weeks_from_end = yearterm_end_datetime - timedelta(days=14)
                is_two_weeks_from_end = datetime.now() >= two_weeks_from_end

                break

        return {"yearterm": yearterm, "is_two_weeks_from_end": is_two_weeks_from_end}
