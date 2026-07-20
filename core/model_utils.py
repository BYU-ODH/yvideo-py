import logging

from django.utils import timezone

from .api import Api
from .models import Course
from .models import PrivilegeLevel
from .models import UserCourses

try:
    from yvideo.secret_settings import ADMIN_BYUID_WHITELIST
except ImportError:
    ADMIN_BYUID_WHITELIST = []

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
            UserCourses.objects.filter(
                user_id=user.id, course_id=course.id, yearterm=yearterm
            )
        )
    except Exception as e:
        log_error(
            "An error occurred while filtering UserCourses objects",
            {"user": user, "course": course, "yearterm": yearterm},
            e,
        )
        return False
    try:
        if not associations:
            UserCourses.objects.create(
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


def update_user_details(user):
    """
    Check if this user is a student or is an employee/faculty member. Update
    the faculty status as necessary. Update this user's first and last names
    if applicable. Also update netid in case something happened to our record
    of it's value (always assume the API's value is correct)
    """
    api = Api()

    def update_student_details(byu_id):
        student_summary = api.get_student_summary(byu_id)
        if student_summary is None:
            # this is a non-student user, do nothing
            return
        user.first_name = student_summary["first_name"]
        user.last_name = student_summary["last_name"]
        user.netid = student_summary["net_id"]
        user.privilege_level = PrivilegeLevel.STUDENT
        try:
            user.save()
            return
        except Exception as e:
            logger.error(f"Failed to update student user data. Exception: {e}")
            return

    worker_id = api.get_worker_id_from_byu_id(user.username)
    if worker_id is None:
        # an error occurred, so don't update anything since the API may be down.
        # In other words, we can't tell if we should run worker_summary or student_summary
        return
    elif worker_id == False:
        # this must be a non-employee student or is a non-student user
        return update_student_details(user.username)
    else:
        # this must be a student employee, staff, or faculty member
        worker_summary = api.get_worker_summary(worker_id, user.username)
        if worker_summary["is_student"]:
            # this is a student after all
            return update_student_details(user.username)
        user.first_name = worker_summary["first_name"]
        user.last_name = worker_summary["last_name"]
        worker_netid = api.get_net_id_from_worker_id(worker_id)
        if worker_netid:
            user.netid = worker_netid

        if user.username in ADMIN_BYUID_WHITELIST:
            user.privilege_level = PrivilegeLevel.ADMIN
        elif not worker_summary["is_active"]:
            user.privilege_level = PrivilegeLevel.STUDENT
        elif worker_summary["is_faculty"]:
            user.privilege_level = PrivilegeLevel.INSTRUCTOR
        elif worker_summary["is_odh_employee"]:
            user.privilege_level = PrivilegeLevel.LAB_ASSISTANT

        try:
            user.save()
        except Exception as e:
            logger.error(f"Failed to update worker user data. Exception: {e}")
            return


def update_user_enrollment(user):
    """
    Update's a user's enrolled courses for the current semester. If the semester
    is 2 weeks or less away from ending, this method will also get the student's
    enrollments for the next semester. If there is an error getting the student's
    enrollments for the current or next semesters, this is noted in the return
    object. A result message is also provided in the result object describing
    what happened and what the user can expect to see.
    """
    update_result = {
        "is_current_sem_updated": False,
        "is_next_sem_updated": False,
        "result_message": "Failed to update user enrollment",
    }
    # don't bother if we don't have a netid for the user
    if user.username is None:
        update_result["result_message"] = "Unknown user"
        return update_result
    # get the current yearterm
    # get courses for the current yearterm
    # if the yearterm is close to ending, get courses for the next yearterm too
    api = Api()
    current_yearterm_lookup = api.get_current_year_term()
    current_yearterm = current_yearterm_lookup["yearterm"]
    next_yearterm = api.calculate_next_year_term(current_yearterm)

    current_user_enrollments = api.get_student_enrollments(
        user.username, current_yearterm
    )

    updated_current_sem_correctly = True
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
        next_yearterm_courses = api.get_student_enrollments(
            user.username, next_yearterm
        )

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

    update_result["is_current_sem_updated"] = updated_current_sem_correctly
    update_result["is_next_sem_updated"] = updated_next_sem_correctly
    update_result["result_message"] = result_message
    return update_result
