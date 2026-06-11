import random

from mozilla_django_oidc.auth import OIDCAuthenticationBackend

from core.api import Api
from core.models import PrivilegeLevel
from core.models import User


def build_random_netid():
    # this is only for dev purposes. this will be removed in the future
    char_list = [
        "a",
        "b",
        "c",
        "d",
        "e",
        "f",
        "g",
        "h",
        "i",
        "j",
        "k",
        "l",
        "m",
        "n",
        "o",
        "p",
    ]
    new_id = random.choices(char_list, k=8)
    return "".join(new_id)


class OIDCUserAuth(OIDCAuthenticationBackend):
    def verify_claims(self, claims):
        if "byu_id" in claims:
            api = Api()
            worker_id = api.get_worker_id_from_byu_id(claims.get("byu_id"))
            student_summary = api.get_student_summary(claims.get("byu_id"))
            if worker_id is None and student_summary is None:
                print(
                    f"Login rejected because the user is not affiliated as an employee or student. Rejected BYU-ID: {claims.get('byu_id')}"
                )
                return False
            return True
        else:
            print("Login rejected. No BYU-ID provided")
            return False

    def create_user(self, claims):
        byu_id = claims.get("byu_id")
        if byu_id is None:
            print("Failed to create a user, no BYU-ID provided")
            return

        # figure out if this is a student or faculty
        api = Api()

        # because faculty members may well have been students, we need to check for faculty status first
        worker_id = api.get_worker_id_from_byu_id(byu_id)
        if worker_id:
            worker_summary = api.get_worker_summary(worker_id, byu_id)
            if worker_summary["is_faculty"]:
                print("User is faculty")
                netid = build_random_netid()
                print(f"New bogus netid for user is: {netid} for byu-id: {byu_id}")
                # Only activate the following lines after we get access to this API - BDR 6/11/2026
                # netid = api.get_net_id_from_worker_id(self, worker_id)
                # user.netid = netid
                # user.byu_id = byu_id
                # return user

                user = User.objects.create(
                    username=byu_id,
                    netid=netid,
                    privilege_level=PrivilegeLevel.INSTRUCTOR,
                    first_name=worker_summary["first_name"],
                    last_name=worker_summary["last_name"],
                )
                return user
            else:
                print("User is not faculty")
                # this could be a current student who has a job at BYU, so user the student_summary instead.
                # One problem with this: ODH staff members (or student employees) that need access to this service
                # will not be automatically configured unless we do more to determine who they are with the provided
                # data. Instead, they will have to be manually configured. We will likely want to change this.

        student_summary = api.get_student_summary(byu_id)
        if student_summary is not None:
            print("User is a student")
            user = User.objects.create(
                username=byu_id,
                netid=student_summary["net_id"],
                first_name=student_summary["first_name"],
                last_name=student_summary["last_name"],
            )
            return user
        else:
            # this is likely an odh staff member or a user who has a byu_id but is otherwise unaffiliated with the university
            print("User did not match a student or faculty member")
            if worker_id:
                worker_summary = api.get_worker_summary(worker_id, byu_id)
                if worker_summary is None:
                    print(
                        "User did not match student, faculty member, or other employee type. Refusing to create a user"
                    )
                    return
                user = User.objects.create(
                    username=byu_id,
                    netid=build_random_netid(),
                    first_name=worker_summary["first_name"],
                    last_name=worker_summary["last_name"],
                )
                return user

        print("Unknown entity with byu_id, refusing to create a user")

    def update_user(self, user, claims):
        # for now, we don't do anything to update the user
        # in the future, we may want to update the user's enrolled
        # courses as this point, if they are a student - BDR 6/10/2026
        return user

    def filter_users_by_claims(self, claims):
        byu_id = claims.get("byu_id")
        if not byu_id:
            return User.objects.none()
        try:
            user = User.objects.get(username=byu_id)
            return [user]
        except User.DoesNotExist:
            return User.objects.none()
