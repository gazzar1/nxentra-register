# accounts/tests/test_pending_login_token.py
"""
A87 regression suite — the browser company-selection step must NOT round-trip
the user's password through the client. Login without a company_id returns a
short-lived signed `pending_login_token`; the second call exchanges that token
+ company_id for JWTs.

These tests pin the contract so the password-in-sessionStorage bug cannot come
back.
"""

import time
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import signing
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import Company, CompanyMembership
from accounts.views import (
    PENDING_LOGIN_TOKEN_MAX_AGE,
    PENDING_LOGIN_TOKEN_SALT,
    mint_pending_login_token,
)

User = get_user_model()


def _disable_throttle():
    """The login endpoint throttles per IP; tests would trip it under load."""
    return mock.patch("accounts.throttles.LoginThrottle.allow_request", return_value=True)


class PendingLoginTokenTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="multi@test.com",
            password="correcthorsebattery",
            email_verified=True,
            is_approved=True,
        )
        self.company_a = Company.objects.create(name="Co A", slug="co-a")
        self.company_b = Company.objects.create(name="Co B", slug="co-b")
        CompanyMembership.objects.create(user=self.user, company=self.company_a, role="OWNER", is_active=True)
        CompanyMembership.objects.create(user=self.user, company=self.company_b, role="OWNER", is_active=True)
        self.url = reverse("accounts:login")

    # -- choose_company response --------------------------------------------

    def test_choose_company_response_includes_signed_pending_token(self):
        with _disable_throttle():
            resp = self.client.post(
                self.url,
                {"email": "multi@test.com", "password": "correcthorsebattery"},
                format="json",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["detail"], "choose_company")
        self.assertIn("pending_login_token", resp.data)
        token = resp.data["pending_login_token"]
        self.assertIsInstance(token, str)
        self.assertGreater(len(token), 20)

        # Token must verify and embed user_id + valid_company_ids only
        payload = signing.loads(token, salt=PENDING_LOGIN_TOKEN_SALT)
        self.assertEqual(payload["user_id"], self.user.id)
        self.assertEqual(
            set(payload["valid_company_ids"]),
            {self.company_a.id, self.company_b.id},
        )
        # Critically: no password, no email, no hash leak in the payload.
        self.assertNotIn("password", payload)
        self.assertNotIn("password_hash", payload)
        self.assertNotIn("email", payload)

    def test_choose_company_response_does_not_echo_password(self):
        """Sanity check: the response body must never contain the password."""
        with _disable_throttle():
            resp = self.client.post(
                self.url,
                {"email": "multi@test.com", "password": "correcthorsebattery"},
                format="json",
            )
        self.assertEqual(resp.status_code, 200)
        body = str(resp.content)
        self.assertNotIn("correcthorsebattery", body)

    # -- exchange path ------------------------------------------------------

    def test_pending_token_exchanges_for_jwts(self):
        token = mint_pending_login_token(
            user_id=self.user.id,
            valid_company_ids=[self.company_a.id, self.company_b.id],
        )
        with _disable_throttle():
            resp = self.client.post(
                self.url,
                {"pending_login_token": token, "company_id": self.company_a.id},
                format="json",
            )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertIn("access", resp.data)
        self.assertIn("refresh", resp.data)

        # Tokens must carry the chosen company_id (tenant binding).
        from rest_framework_simplejwt.tokens import AccessToken

        access = AccessToken(resp.data["access"])
        self.assertEqual(access["company_id"], str(self.company_a.id))
        # active_company_id is now set on the user record.
        self.user.refresh_from_db()
        self.assertEqual(self.user.active_company_id, self.company_a.id)

    def test_pending_token_rejects_company_not_in_payload(self):
        """A token minted for companies A+B must not be usable for company C."""
        other_company = Company.objects.create(name="Co C", slug="co-c")
        token = mint_pending_login_token(
            user_id=self.user.id,
            valid_company_ids=[self.company_a.id, self.company_b.id],
        )
        with _disable_throttle():
            resp = self.client.post(
                self.url,
                {"pending_login_token": token, "company_id": other_company.id},
                format="json",
            )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.data["detail"], "invalid_company")

    def test_pending_token_rejects_revoked_membership(self):
        """Token still valid but membership got deactivated between step 1 and 2."""
        token = mint_pending_login_token(
            user_id=self.user.id,
            valid_company_ids=[self.company_a.id, self.company_b.id],
        )
        CompanyMembership.objects.filter(user=self.user, company=self.company_a).update(is_active=False)

        with _disable_throttle():
            resp = self.client.post(
                self.url,
                {"pending_login_token": token, "company_id": self.company_a.id},
                format="json",
            )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.data["detail"], "invalid_company")

    def test_pending_token_rejects_tampered_payload(self):
        token = mint_pending_login_token(
            user_id=self.user.id,
            valid_company_ids=[self.company_a.id],
        )
        # Flip a character in the middle (after the payload, before the sig).
        tampered = token[:-5] + ("X" if token[-5] != "X" else "Y") + token[-4:]
        with _disable_throttle():
            resp = self.client.post(
                self.url,
                {"pending_login_token": tampered, "company_id": self.company_a.id},
                format="json",
            )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.data["detail"], "invalid_pending_token")

    def test_pending_token_rejects_expired_token(self):
        token = mint_pending_login_token(
            user_id=self.user.id,
            valid_company_ids=[self.company_a.id],
        )
        # Patch the verifier's max_age to 0 so any age (including ours) is expired.
        with _disable_throttle(), mock.patch("accounts.views.PENDING_LOGIN_TOKEN_MAX_AGE", 0):
            # Force at least 1 second of age so signing considers it expired.
            time.sleep(1)
            resp = self.client.post(
                self.url,
                {"pending_login_token": token, "company_id": self.company_a.id},
                format="json",
            )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.data["detail"], "invalid_pending_token")

    def test_pending_token_requires_company_id(self):
        token = mint_pending_login_token(
            user_id=self.user.id,
            valid_company_ids=[self.company_a.id],
        )
        with _disable_throttle():
            resp = self.client.post(
                self.url,
                {"pending_login_token": token},
                format="json",
            )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data["detail"], "company_id_required")

    def test_pending_token_rejects_user_that_no_longer_exists(self):
        token = mint_pending_login_token(
            user_id=999_999_999,
            valid_company_ids=[self.company_a.id],
        )
        with _disable_throttle():
            resp = self.client.post(
                self.url,
                {"pending_login_token": token, "company_id": self.company_a.id},
                format="json",
            )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.data["detail"], "invalid_pending_token")

    # -- coverage: salt is namespaced ---------------------------------------

    def test_token_signed_with_different_salt_is_rejected(self):
        """A token from a different signing surface must not authenticate here."""
        rogue = signing.dumps(
            {"user_id": self.user.id, "valid_company_ids": [self.company_a.id]},
            salt="some-other-flow",
        )
        with _disable_throttle():
            resp = self.client.post(
                self.url,
                {"pending_login_token": rogue, "company_id": self.company_a.id},
                format="json",
            )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.data["detail"], "invalid_pending_token")

    # -- constants sanity ---------------------------------------------------

    def test_max_age_is_short(self):
        """If someone bumps this above 15 minutes, that's a security regression."""
        self.assertLessEqual(PENDING_LOGIN_TOKEN_MAX_AGE, 900)
