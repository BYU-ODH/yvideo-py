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

    def get_worker_id_from_byu_id(self, byu_id):
        url = secret_settings.API_WORKER_ID_IAM_URL + "?byu_id=" + byu_id
        headers = {"Authorization": self.build_auth_header()}
        worker_id_request = requests.get(url, headers=headers)
        worker_id_json_response = worker_id_request.json()
        response_data = worker_id_json_response["data"]
        worker_id = None
        if len(response_data) > 0:
            worker_id = response_data[0]["worker_id"]

        return worker_id

    # byu_id is passed into this so we can record it later. byu_id does not return in the response.
    def get_worker_summary(self, worker_id, byu_id):
        url = secret_settings.API_WORKER_SUMMARY_URL + "?worker_id=" + worker_id
        headers = {"Authorization": self.build_auth_header()}
        summary_request = requests.get(url, headers=headers)
        summary_json_res = summary_request.json()
        response_data = summary_json_res["data"]
        parsed_summary = {
            "first_name": "",
            "last_name": "",
            "email": "",
            "is_faculty": False,
            "worker_id": worker_id,
            "byu_id": byu_id,
        }
        if response_data:
            data = response_data[0]
            worker_positions = data["positions"]
            parsed_summary["first_name"] = data["preferred_first_name"]
            parsed_summary["last_name"] = data["preferred_last_name"]
            parsed_summary["email"] = data["work_email_address"]
            faculty_keyword = "faculty"
            for position in worker_positions:
                position_code = position[
                    "employee_or_contingent_worker_type_reference_id"
                ]
                position_profile = position["job_profile"].lower()
                position_title = position["business_title"].lower()
                if (
                    position_code == "FAC"
                    or faculty_keyword in position_profile
                    or faculty_keyword in position_title
                ):
                    parsed_summary["is_faculty"] = True

            return parsed_summary
        else:
            return None

    def get_student_summary(self, byu_id):
        url = secret_settings.API_STUDENT_SUMMARY_URL + "?byu_id=" + byu_id
        headers = {"Authorization": self.build_auth_header()}
        summary_request = requests.get(url, headers=headers)
        summary_json_res = summary_request.json()
        data = summary_json_res["data"]
        if data:
            data = data[0]
            preferred_first_name = data["preferred_name"].split(" ")[0]
            parsed_summary = {
                "first_name": preferred_first_name,
                "last_name": data["preferred_last_name"],
                "email": data["student_email_address"],
                "is_faculty": False,
                "byu_id": byu_id,
                "net_id": data["net_id"],
            }
            return parsed_summary
        else:
            return None

    def get_student_enrollments(self, net_id, yearterm):
        url = (
            secret_settings.API_STUDENT_ENROLLMENTS_URL
            + "?net_id="
            + net_id
            + "&year_term="
            + yearterm
        )
        headers = {"Authorization": self.build_auth_header()}
        records_request = requests.get(url, headers=headers)
        records_json_res = records_request.json()
        records_data = records_json_res["data"]
        if records_data:
            parsed_records = []
            for record in records_data:
                parsed_records.append(
                    {
                        "curriculum_id": record["curriculum_id"],
                        "title_code": record["title_code"],
                        "section_number": record["section_number"],
                        "teaching_area": record["teaching_area"],
                        "catalog_number": record["catalog_number"],
                        "catalog_suffix": record["catalog_suffix"],
                        "credit_hours": record["credit_hours"],
                        "withdraw_flag": record["withdraw_flag"],
                        "audit_flag": record["audit_flag"],
                    }
                )
            return parsed_records
        else:
            return None
