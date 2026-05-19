from .views import _has_academic_term, _has_profile_photo, _has_program_and_curriculum, get_logged_in_student
from .superuser_student_portal import is_superuser_demo_session


def academic_term_context(request):
    """Expose term and profile completeness for navigation."""
    has_ay = _has_academic_term(request)
    has_photo = _has_profile_photo(request)
    student = get_logged_in_student(request)
    has_prog_cur = _has_program_and_curriculum(student)
    return {
        'has_academic_term': has_ay,
        'has_program_curriculum': has_prog_cur,
        'has_complete_info': has_ay and has_photo and has_prog_cur,
    }


def superuser_demo_context(request):
    return {'is_superuser_demo': is_superuser_demo_session(request)}
