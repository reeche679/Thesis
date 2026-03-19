from .views import _has_academic_term, _has_profile_photo


def academic_term_context(request):
    """Expose term and profile completeness for navigation."""
    has_ay = _has_academic_term(request)
    has_photo = _has_profile_photo(request)
    return {
        'has_academic_term': has_ay,
        'has_complete_info': has_ay and has_photo,
    }
