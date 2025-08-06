from django.utils import timezone

from .api import Api
from .models import Course
from .models import User
from .models import UserCourse

CORE_ERROR_LOG_PATH = "./.yvideo-py-core-error.log"


def log_error(error_message, error_origin, error_info={}, exception=None):
    """
    Writes an error to the specified error log path. Includes datetime error is reported and
    which function generated the error. If a python exception is provided, this is also logged.
    """

    error_time = timezone.now()
    with open(CORE_ERROR_LOG_PATH, "a") as error_log:
        error_log.write(
            f"{error_message}\nfrom {error_origin}\nTime: {error_time}\nError information: {error_info}\n"
            + ""
            if exception is None
            else f"Exception: {exception}\n\n"
        )


def check_for_user_in_db(byu_id):
    try:
        user = User.objects.get(byu_id=byu_id)
        return user
    except Exception:
        return None


def check_or_create_course(course):
    # check if a course exists, if it doesn't, create it
    try:
        course_obj = Course.objects.filter(
            dept=course["teaching_area"],
            catalog_number=course["catalog_number"] + course["catalog_suffix"],
            section_number=course["section_number"],
        )
    except Course.DoesNotExist:
        try:
            course_obj = Course.objects.create(
                dept=course["teaching_area"],
                catalog_number=course["catalog_number"] + course["catalog_suffix"],
                section_number=course["section_number"],
            )
        except Exception as e:
            log_error(
                "An error occurred while creating a new course",
                "core/logic.py check_or_create_course",
                {"course_info": course},
                e,
            )
            return None
    return course_obj


def create_user_course_association(user, course, yearterm):
    # check if association already exists
    associations = list(
        UserCourse.objects.filter(
            user_id=user.id, course_id=course.id, yearterm=yearterm
        )
    )
    if associations:
        return

    try:
        UserCourse.objects.create(
            user_id=user.id, course_id=course.id, yearterm=yearterm
        )
    except Exception as e:
        log_error(
            "An error occurred while associating a user with a course",
            "core/logic.py create_user_course_association",
            {"user": user, "course": course, "yearterm": yearterm},
            e,
        )
        return False
    return True


def update_user_enrollment(user):
    # don't bother if we don't have a netid for the user
    if user.netid is None:
        return
    # get the current yearterm
    # get courses for the current yearterm
    # if the yearterm is close to ending, get courses for the next yearterm too
    api = Api()
    current_yearterm_lookup = api.get_current_year_term
    current_yearterm = current_yearterm_lookup["yearterm"]
    next_yearterm = api.calculate_next_year_term(current_yearterm)

    current_user_enrollments = api.get_student_enrollments(user.netid, current_yearterm)
    # check that each course exists, if it doesn't, create it
    for course in current_user_enrollments:
        result = check_or_create_course(course)
        if result is None:
            continue

        create_user_course_association(user, course, current_yearterm)

    if current_yearterm_lookup["is_two_weeks_from_end"]:
        next_yearterm_courses = api.get_student_enrollments(user.netid, next_yearterm)
        for course in next_yearterm_courses:
            result = check_or_create_course(course)
            if result is None:
                continue
            create_user_course_association(user, course, current_yearterm)


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
    update_user_enrollment(user)
    return result
