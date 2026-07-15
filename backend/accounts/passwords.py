"""
Canonical password rules.

Owner decision 2026-07-15: explicit, merchant-legible checklist rules rather
than opaque validators — a rejected password must always map to a rule the
user can see. Enforced on every application password-setting path (register,
invitation accept, user create, self change, admin reset); the register form
mirrors the same four rules as a live checklist (frontend/lib/password-rules.ts).

Deliberately exempt: Django admin forms (AUTH_PASSWORD_VALIDATORS min-8 still
applies there, superuser-only) and UserManager.create_user/create_superuser
(internal API for tests/seeds/shell).

Existing password hashes are never re-validated — rules apply only when a
password is set.

Semantics must stay in lockstep with the frontend mirror:
- "special character" = anything that is not an ASCII letter or digit, so a
  non-Latin letter (e.g. Arabic) counts as special on BOTH sides.
- length is counted in code points on both sides.
- The client's uppercase/number checks are ASCII-only and therefore stricter
  than isupper()/isdigit() here — that direction is safe (a password the
  checklist approves is never rejected by the server).
"""


def _is_special(character: str) -> bool:
    return not (character.isascii() and character.isalnum())


def password_rule_errors(password: str) -> list[str]:
    """Return one message per unmet rule; empty list means the password passes."""
    password = password or ""
    errors: list[str] = []
    if len(password) < 8:
        errors.append("Password must be at least 8 characters.")
    if not any(character.isupper() for character in password):
        errors.append("Password must include at least one uppercase letter.")
    if not any(character.isdigit() for character in password):
        errors.append("Password must include at least one number.")
    if not any(_is_special(character) for character in password):
        errors.append("Password must include at least one special character.")
    return errors
