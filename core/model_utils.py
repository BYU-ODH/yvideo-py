import logging

from django.utils import timezone

from .api import Api
from .models import Course
from .models import User
from .models import UserCourse

logger = logging.getLogger(__name__)


def log_error(error_message, error_info={}, exception=None):
    """
    Writes an error to the specified error log path. Includes datetime error is reported and
    which function generated the error. If a python exception is provided, this is also logged.
    """
    error_time = timezone.now()
    logger.error(
        f"{error_message}\nTime: {error_time}\nError information: {error_info}\n" + ""
        if exception is None
        else f"Exception: {exception}\n\n"
    )


def check_for_user_in_db(byu_id):
    """
    Checks if there is a user associated with the provided byu_id. If there is,
    the user will be returned. If there is no user associated, False will be returned.
    If an error occurs while checking for a user, None will be returned and the error
    will be logged.
    """
    try:
        user = User.objects.get(byu_id=byu_id)
        return user
    except User.DoesNotExist:
        return False
    except Exception as e:
        log_error(
            "An error occurred while checking for the existance of a user",
            {"byu_id": byu_id},
            e,
        )
        return None


def get_or_create_course(course):
    """
    Checks if the course already exists, if not, it will create it. The
    pre-existing course, or new course will be returned unless there is
    an error. If an error occurs, returns None.
    """
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
                {"course_info": course},
                e,
            )
            return None
    return course_obj


def create_user_course_association(user, course, yearterm):
    """
    Check if there is already a user-course association. If an association
    exists or if one was created, return True. If an error occured, return False
    """
    try:
        associations = list(
            UserCourse.objects.filter(
                user_id=user.id, course_id=course.id, yearterm=yearterm
            )
        )
        if not associations:
            UserCourse.objects.create(
                user_id=user.id, course_id=course.id, yearterm=yearterm
            )
    except Exception as e:
        log_error(
            "An error occurred while associating a user with a course",
            {"user": user, "course": course, "yearterm": yearterm},
            e,
        )
        return False
    return True


def update_user_enrollment(user):
    """
    Update's a user's enrolled courses for the current semester. If the semester
    is 2 weeks or less away from ending, this method will also get the student's
    enrollments for the next semester. If there is an error getting the student's
    enrollments for the current or next semesters, this is noted in the return
    object. A result message is also provided in the result object describing
    what happened and what the user can expect to see.
    """
    # don't bother if we don't have a netid for the user
    if user.netid is None:
        return None
    # get the current yearterm
    # get courses for the current yearterm
    # if the yearterm is close to ending, get courses for the next yearterm too
    api = Api()
    current_yearterm_lookup = api.get_current_year_term
    current_yearterm = current_yearterm_lookup["yearterm"]
    next_yearterm = api.calculate_next_year_term(current_yearterm)

    current_user_enrollments = api.get_student_enrollments(user.netid, current_yearterm)

    if current_user_enrollments is None:
        updated_current_sem_correctly = False
    else:
        for course in current_user_enrollments:
            result = get_or_create_course(course)
            if result is None:
                updated_current_sem_correctly = False
                continue

            if not create_user_course_association(user, course, current_yearterm):
                updated_current_sem_correctly = False

    updated_next_sem_correctly = True
    if current_yearterm_lookup["is_two_weeks_from_end"]:
        next_yearterm_courses = api.get_student_enrollments(user.netid, next_yearterm)

        if next_yearterm_courses is None:
            updated_next_sem_correctly = False
        else:
            for course in next_yearterm_courses:
                result = get_or_create_course(course)
                if result is None:
                    updated_next_sem_correctly = False
                    continue

                if not create_user_course_association(user, course, current_yearterm):
                    updated_next_sem_correctly = False

    result_message = ""
    if not updated_current_sem_correctly or not updated_next_sem_correctly:
        result_message = "Failed to update the "
        result_message += (
            "current semester's " if not updated_current_sem_correctly else ""
        )
        result_message += (
            "and "
            if not updated_current_sem_correctly and not updated_next_sem_correctly
            else ""
        )
        result_message += "next semester's " if not updated_next_sem_correctly else ""
        result_message += "enrollment correctly. Some courses may be missing, and you may see previously enrolled courses."

    return {
        "is_current_sem_udpated": updated_current_sem_correctly,
        "is_next_sem_updated": updated_next_sem_correctly,
        "result_message": result_message,
    }


def create_or_update_user(byu_id):
    """
    Checks if a user tied to the provided byu_id already exists. If not,
    creates a new user. The returned object provides the user and
    whether the user is newly created.
    """
    result = {
        "is_new_user_created": False,
        "user": None,
        "enrollment_update_message": "Course enrollment was not updated",
    }
    # check if user already exists, if they do, return it
    try:
        user = User.objects.get(byu_id=byu_id)
        result["user"] = user
        update_result = update_user_enrollment(user)
        result["enrollment_update_message"] = update_result["result_message"]
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
    update_result = update_user_enrollment(user)
    result["enrollment_update_message"] = update_result["result_message"]
    return result
