"""
F2 — a single-company user shouldn't be forced through a one-option chooser.

Login WITHOUT a company_id used to ALWAYS return "choose_company". Now, when the
user has exactly one active membership, the backend auto-selects it and issues
tokens directly; multi-company users still get the chooser.
"""

import pytest
from rest_framework.test import APIClient

from accounts.models import CompanyMembership

LOGIN_URL = "/api/auth/login/"
PASSWORD = "Testpass123!"


def _loginable(user):
    """Clear the verification + beta-gate checks that run before company logic."""
    user.email_verified = True
    user.is_approved = True
    user.save(update_fields=["email_verified", "is_approved"])
    return user


@pytest.mark.django_db
def test_single_company_login_issues_tokens_directly(user, owner_membership):
    _loginable(user)
    resp = APIClient().post(LOGIN_URL, {"email": user.email, "password": PASSWORD}, format="json")
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body.get("detail") != "choose_company"
    assert body.get("access")
    assert body.get("refresh")


@pytest.mark.django_db
def test_multi_company_login_still_shows_chooser(user, owner_membership, second_company):
    _loginable(user)
    # A second active membership means the user must pick which company.
    CompanyMembership.objects.create(
        user=user,
        company=second_company,
        role=CompanyMembership.Role.OWNER,
        is_active=True,
    )
    resp = APIClient().post(LOGIN_URL, {"email": user.email, "password": PASSWORD}, format="json")
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["detail"] == "choose_company"
    assert len(body["companies"]) == 2
    assert "access" not in body  # no tokens until a company is chosen


@pytest.mark.django_db
def test_no_company_user_is_refused(user):
    _loginable(user)
    # No membership at all — still refused (unchanged).
    resp = APIClient().post(LOGIN_URL, {"email": user.email, "password": PASSWORD}, format="json")
    assert resp.status_code == 403
    assert resp.json().get("detail") == "no_company_access"
