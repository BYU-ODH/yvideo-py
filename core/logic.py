from .api import Api
from .models import User


def check_for_user_in_db(byu_id):
    try:
        user = User.objects.get(byu_id=byu_id)
        return user
    except Exception:
        return None


def create_new_user(byu_id):
    result = {"is_new_user_created": False, "user": None}
    # check if user already exists, if they do, return it
    try:
        user = User.objects.get(byu_id=byu_id)
        result["user"] = user
        return result
    except User.DoesNotExist:
        pass

    # We must determine if the user is a worker, or a student and call the correct summary
    api = Api()
    worker_id = api.get_worker_id_from_byu_id(byu_id)
    summary = None
    if worker_id is not None:
        summary = api.get_worker_summary(worker_id, byu_id)

    # this is a separate if because the summary from get_worker_summary may also return None
    # this happens if the person we think is a worker, is actually NOT a worker
    if summary is None:
        summary = api.get_student_summary(byu_id)

    if summary is None:
        # something went wrong, we have no data, abandon user creation
        return result
    netid = summary["netid"] if "netid" in summary else ""
    privilege_level = 2 if summary["is_faculty"] else 3
    user = User.objects.create(
        netid=netid, byu_id=byu_id, privilege_level=privilege_level
    )
    result["user"] = user
    result["is_new_user_created"] = True
    return result


def update_user_enrollment(byu_id):
    pass
