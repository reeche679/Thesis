"""Helpers for curriculum manager permissions (Phase 1)."""


def user_can_manage_program_curriculum(user, program) -> bool:
    if program is None:
        return False
    return program.user_can_manage_versions(user)
