"""
Canonical password rules.

Owner decision 2026-07-15: explicit, merchant-legible checklist rules rather
than opaque validators — a rejected password must always map to a rule the
user can see. Enforced on every password-setting path (register, invitation
accept, self change, admin reset); the register form mirrors the same four
rules as a live checklist (frontend/lib/password-rules.ts).

Existing password hashes are never re-validated — rules apply only when a
password is set.
"""


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
    if not any(not character.isalnum() for character in password):
        errors.append("Password must include at least one special character.")
    return errors
