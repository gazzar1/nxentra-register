#!/usr/bin/env python
"""
Debug script to test email sending functionality.
Run this on the server to diagnose email issues:

    cd /var/www/nxentra_app/backend
    source venv/bin/activate
    python test_email_debug.py
"""
import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nxentra_backend.settings')
django.setup()

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils.html import strip_tags


def main():
    test_email = input("Enter your email address to receive test emails: ").strip()
    if not test_email:
        print("Email is required.")
        return

    print("\n" + "=" * 60)
    print("EMAIL CONFIGURATION")
    print("=" * 60)
    print(f"EMAIL_BACKEND: {settings.EMAIL_BACKEND}")
    print(f"DEFAULT_FROM_EMAIL: {settings.DEFAULT_FROM_EMAIL}")
    print(f"FRONTEND_URL: {getattr(settings, 'FRONTEND_URL', 'NOT SET')}")
    print(f"VERIFICATION_TOKEN_EXPIRY_HOURS: {getattr(settings, 'VERIFICATION_TOKEN_EXPIRY_HOURS', 'NOT SET')}")
    print(f"POSTMARK TOKEN exists: {'Yes' if getattr(settings, 'POSTMARK', {}).get('TOKEN') else 'NO!'}")
    print()

    # Test 1: Simple send_mail
    print("=" * 60)
    print("TEST 1: Basic send_mail (no template)")
    print("=" * 60)
    try:
        result = send_mail(
            subject="Test 1: Basic email",
            message="This is a basic test email without HTML.",
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[test_email],
            fail_silently=False,
        )
        print(f"Result: {result} (1 = success)")
    except Exception as e:
        print(f"FAILED: {type(e).__name__}: {e}")
    print()

    # Test 2: Template rendering
    print("=" * 60)
    print("TEST 2: Template rendering")
    print("=" * 60)
    try:
        context = {
            "user_name": "Test User",
            "verification_url": "https://app.nxentra.com/verify-email?token=TEST123",
            "expiry_hours": 24,
        }
        html_message = render_to_string("emails/verify_email.html", context)
        print(f"Template rendered successfully! Length: {len(html_message)} characters")
        print(f"First 100 chars: {html_message[:100]}...")
    except Exception as e:
        print(f"FAILED: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
    print()

    # Test 3: Full verification email (like the registration flow)
    print("=" * 60)
    print("TEST 3: Full verification email (with template)")
    print("=" * 60)
    try:
        context = {
            "user_name": "Test User",
            "verification_url": "https://app.nxentra.com/verify-email?token=TEST123",
            "expiry_hours": 24,
        }
        html_message = render_to_string("emails/verify_email.html", context)
        plain_message = strip_tags(html_message)

        result = send_mail(
            subject="Test 3: Verify your Nxentra account",
            message=plain_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[test_email],
            html_message=html_message,
            fail_silently=False,
        )
        print(f"Result: {result} (1 = success)")
    except Exception as e:
        print(f"FAILED: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
    print()

    # Test 4: Using the actual email_service function
    print("=" * 60)
    print("TEST 4: Using accounts.email_service.send_verification_email")
    print("=" * 60)
    try:
        from accounts.email_service import send_verification_email

        # Create a mock user object
        class MockUser:
            def __init__(self, email, name=None):
                self.email = email
                self.name = name

        mock_user = MockUser(test_email, "Test User")
        result = send_verification_email(mock_user, "FAKE_TOKEN_12345")
        print(f"Result: {result} (True = success)")
    except Exception as e:
        print(f"FAILED: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
    print()

    print("=" * 60)
    print("Check your inbox for the test emails!")
    print("=" * 60)


if __name__ == "__main__":
    main()
