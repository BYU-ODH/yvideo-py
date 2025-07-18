# This file defines the functions used to request data from OIT's APIs

import requests
import datetime


# a valid auth token is required to access data from OIT APIs
# this token is packaged as a "Bearer" token in the "Authorization"
# request header. This method builds and returns that Bearer token header
def build_auth_header(client_id, client_secret):
    # request an auth token from OIT's auth token granting endpoint
    token_request = requests.post(
        "https://api.byu.edu/token",
        data={"grant_type": "client_credentials"},
        auth=(client_id, client_secret),
    )
    token_json_result = token_request.json()
    access_token = token_json_result["access_token"]
    auth_header = f"Bearer {access_token}"
    return auth_header


def get_current_year_term(bearer_token_string):
    # to determine current year term, we have to compare to today's date
    today_datetime = datetime.datetime.today().strftime("%Y-%m-%dT%H:%M:%S")

    # get yearterm information
    url = "https://api.byu.edu/bdp/student_academics/academic_control_dates/v1/?control_date_type=CURRICULUM"
    headers = {"Authorization": bearer_token_string}
    control_date_request = requests.get(url, headers=headers)
    control_date_json_response = control_date_request.json()
    response_data = control_date_json_response["data"]

    # determine which yearterm corresponds to current datetime
    for entry in response_data:
        if (
            entry["start_date_time"] <= today_datetime
            and entry["end_date_time"] > today_datetime
        ):
            yearterm = entry["year_term"]
            break

    return yearterm
