# This file defines the functions used to request data from OIT's APIs

from datetime import datetime
from datetime import timedelta
import logging

from django.db import transaction
from django.utils import timezone
import requests

try:
    import yvideo.secret_settings as secret_settings
except ImportError:
    import warnings

    warnings.warn(
        "secret_settings.py not found; falling back to secret_settings_template.py",
        stacklevel=1,
    )
    import yvideo.secret_settings_template as secret_settings

from .models import AuthToken


class Api:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        auth_tokens = AuthToken.objects.all()
        auth_tokens_count = len(list(auth_tokens))

        # don't allow auth token to be older than 1 hour
        oldest_valid_time = timezone.now() - timedelta(hours=1)

        # filter for tokens that have a creation date greater than (__gt) the oldest valid time
        valid_auth_tokens = AuthToken.objects.filter(created_at__gt=oldest_valid_time)
        valid_auth_tokens_count = len(list(valid_auth_tokens))

        if auth_tokens_count == 1 and valid_auth_tokens_count == 1:
            # there is only one token, and it is valid
            self.auth_token = auth_tokens.first().token
        else:
            # there are either more than 1 token, or that token is invalid
            # either way, delete everything and generate a new token
            auth_token = self.generate_auth_token()
            with transaction.atomic():
                auth_tokens.delete()
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

    @staticmethod
    def parse_api_datetime(value):
        # BYU API timestamps omit timezone information but should be interpreted
        # as server-local time, i.e. the current Django timezone.
        return timezone.make_aware(
            datetime.strptime(value, "%Y-%m-%dT%H:%M:%S"),
            timezone.get_current_timezone(),
        )

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

    def get_year_terms(self):
        """Every yearterm the API knows about, with its start and end datetimes.

        Returns a list of {"yearterm", "start_date_time", "end_date_time"} dicts.
        Entries the API returns without parseable dates are skipped.
        """
        url = secret_settings.API_YEARTERM_URL
        headers = {"Authorization": self.build_auth_header()}
        control_date_request = requests.get(url, headers=headers)
        control_date_json_response = control_date_request.json()
        response_data = control_date_json_response["data"]

        year_terms = []
        for entry in response_data:
            try:
                year_terms.append(
                    {
                        "yearterm": entry["year_term"],
                        "start_date_time": self.parse_api_datetime(
                            entry["start_date_time"]
                        ),
                        "end_date_time": self.parse_api_datetime(
                            entry["end_date_time"]
                        ),
                    }
                )
            except (KeyError, TypeError, ValueError) as e:
                self.logger.error(f"Skipping unparseable yearterm entry {entry}: {e}")
        return year_terms

    def get_current_year_term(self):
        now = timezone.now()

        # determine which yearterm corresponds to current datetime
        yearterm = None
        is_two_weeks_from_end = False
        for entry in self.get_year_terms():
            if entry["start_date_time"] <= now < entry["end_date_time"]:
                yearterm = entry["yearterm"]
                # determine if the end of the current year term is 2 weeks or less away
                two_weeks_from_end = entry["end_date_time"] - timedelta(days=14)
                is_two_weeks_from_end = now >= two_weeks_from_end

                break

        return {"yearterm": yearterm, "is_two_weeks_from_end": is_two_weeks_from_end}

    def get_worker_id_from_byu_id(self, byu_id):
        url = secret_settings.API_WORKER_ID_IAM_URL + "?byu_id=" + byu_id
        headers = {"Authorization": self.build_auth_header()}
        worker_id_request = requests.get(url, headers=headers)
        if worker_id_request.status_code != 200:
            self.logger.error(
                "Failed to get workerid from byuid because the API responded with an error"
            )
            return None

        worker_id_json_response = worker_id_request.json()
        response_data = worker_id_json_response["data"]
        try:
            worker_id = response_data[0]["worker_id"]
        except (IndexError, KeyError):
            # No worker record for this BYU ID, so this is not an employee.
            return False

        return worker_id

    def get_net_id_from_worker_id(self, worker_id):
        url = secret_settings.API_NET_ID_IAM_URL + "?worker_id=" + worker_id
        headers = {"Authorization": self.build_auth_header()}
        net_id_request = requests.get(url, headers=headers)
        if net_id_request.status_code != 200:
            self.logger.error(
                "Failed to get netid from worker id because the API responded with an error"
            )
            return None

        net_id_json_response = net_id_request.json()
        response_data = net_id_json_response["data"]
        net_id = None
        if len(response_data) > 0:
            net_id = response_data[0]["net_id"]
        else:
            self.logger.error(
                "Failed to get netid because the API did not return a netid"
            )

        return net_id

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
            "is_student": False,
            "is_odh_employee": False,
            "worker_id": worker_id,
            "byu_id": byu_id,
            "is_active": False,
            "is_retired": False,
        }
        if response_data:
            data = response_data[0]
            worker_positions = data["positions"]
            first_name = ""
            if data["preferred_first_name"]:
                first_name = data["preferred_first_name"]
            elif data["first_name"]:
                first_name = data["first_name"]
            parsed_summary["first_name"] = first_name
            last_name = ""
            if data["preferred_last_name"]:
                last_name = data["preferred_last_name"]
            elif data["last_name"]:
                last_name = data["last_name"]
            parsed_summary["last_name"] = last_name
            parsed_summary["email"] = data["work_email_address"]
            parsed_summary["is_active"] = data["is_active"]
            parsed_summary["is_retired"] = data["is_retired"]
            faculty_keyword = "faculty"
            for position in worker_positions:
                if not position["is_active_position"]:
                    continue
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
                elif position_code == "STD":
                    parsed_summary["is_student"] = True

                if (
                    position["supervisory_org"]
                    == "Humanities, Dean's - Office of Digital Humanities"
                ):
                    parsed_summary["is_odh_employee"] = True

            return parsed_summary
        else:
            return None

    def get_student_summary(self, byu_id=None, net_id=None):
        # Accepts either identifier; the endpoint returns both byu_id and net_id
        # in the response regardless of which one was used to query it.
        if byu_id is not None:
            url = secret_settings.API_STUDENT_SUMMARY_URL + "?byu_id=" + byu_id
        elif net_id is not None:
            url = secret_settings.API_STUDENT_SUMMARY_URL + "?net_id=" + net_id
        else:
            return None
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
                "byu_id": data["byu_id"],
                "net_id": data["net_id"],
            }
            return parsed_summary
        else:
            return None

    def get_student_enrollments(self, net_id, yearterm):
        """This student's enrollment records for one term, or None if the lookup failed.

        An empty list is an answer -- the student is enrolled in nothing this term --
        and callers revoke course access on it. None means we do not know, so callers
        must leave existing access alone. Anything we cannot parse makes the whole
        lookup a failure for that reason: a partial list reads as a set of drops.
        """
        url = (
            secret_settings.API_STUDENT_ENROLLMENTS_URL
            + "?net_id="
            + net_id
            + "&year_term="
            + yearterm
        )
        headers = {"Authorization": self.build_auth_header()}
        records_request = requests.get(url, headers=headers)
        if records_request.status_code != 200:
            self.logger.error(
                f"Failed to get enrollments for {yearterm} because the API responded "
                f"with {records_request.status_code}"
            )
            return None

        try:
            records_data = records_request.json()["data"]
        except (ValueError, KeyError, TypeError) as e:
            self.logger.error(f"Could not read the enrollments response: {e}")
            return None

        parsed_records = []
        for record in records_data or []:
            try:
                parsed_records.append(
                    {
                        key: record[key]
                        for key in [
                            "curriculum_id",
                            "title_code",
                            "section_number",
                            "teaching_area",
                            "catalog_number",
                            "catalog_suffix",
                            "credit_hours",
                            "withdraw_flag",
                            "audit_flag",
                        ]
                    }
                )
            except (KeyError, TypeError) as e:
                self.logger.error(f"Unreadable enrollment record {record}: {e}")
                return None
        return parsed_records
