# accounts/email_service.py
"""
Email service for Nxentra authentication.

Handles:
- Email verification emails
- Admin notification emails
- User approval/rejection notifications

All emails are sent from DEFAULT_FROM_EMAIL (no-reply@nxentra.com).
Admin notifications go to ADMIN_EMAIL (admin@nxentra.com).
"""

import logging
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.conf import settings

logger = logging.getLogger(__name__)


def send_verification_email(user, token: str) -> bool:
    """
    Send email verification link to user.

    Args:
        user: User model instance
        token: Raw verification token (not the hash)

    Returns:
        True if email was sent successfully, False otherwise
    """
    verification_url = f"{settings.FRONTEND_URL}/verify-email?token={token}"

    context = {
        "user": user,
        "user_name": user.name or user.email.split("@")[0],
        "verification_url": verification_url,
        "expiry_hours": settings.VERIFICATION_TOKEN_EXPIRY_HOURS,
    }

    try:
        html_message = render_to_string("emails/verify_email.html", context)
        plain_message = strip_tags(html_message)

        send_mail(
            subject="Verify your Nxentra account",
            message=plain_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            html_message=html_message,
            fail_silently=False,
        )
        logger.info(f"Verification email sent to {user.email}")
        return True
    except Exception as e:
        logger.error(f"Failed to send verification email to {user.email}: {e}")
        return False


def send_admin_approval_notification(user) -> bool:
    """
    Notify admin of new user pending approval.

    Sent when a user verifies their email and Beta Gate is enabled.

    Args:
        user: User model instance

    Returns:
        True if email was sent successfully, False otherwise
    """
    # Get user's company name (from first membership)
    membership = user.memberships.first()
    company_name = membership.company.name if membership else "Unknown"

    admin_url = f"{settings.FRONTEND_URL}/admin/pending-users"

    context = {
        "user": user,
        "user_email": user.email,
        "user_name": user.name or user.email.split("@")[0],
        "company_name": company_name,
        "admin_url": admin_url,
    }

    try:
        html_message = render_to_string("emails/admin_approval_request.html", context)
        plain_message = strip_tags(html_message)

        send_mail(
            subject=f"New user pending approval: {user.email}",
            message=plain_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[settings.ADMIN_EMAIL],
            html_message=html_message,
            fail_silently=False,
        )
        logger.info(f"Admin approval notification sent for {user.email}")
        return True
    except Exception as e:
        logger.error(f"Failed to send admin notification for {user.email}: {e}")
        return False


def send_approval_notification(user) -> bool:
    """
    Notify user their account has been approved.

    Args:
        user: User model instance

    Returns:
        True if email was sent successfully, False otherwise
    """
    login_url = f"{settings.FRONTEND_URL}/login"

    context = {
        "user": user,
        "user_name": user.name or user.email.split("@")[0],
        "login_url": login_url,
    }

    try:
        html_message = render_to_string("emails/user_approved.html", context)
        plain_message = strip_tags(html_message)

        send_mail(
            subject="Your Nxentra account has been approved!",
            message=plain_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            html_message=html_message,
            fail_silently=False,
        )
        logger.info(f"Approval notification sent to {user.email}")
        return True
    except Exception as e:
        logger.error(f"Failed to send approval notification to {user.email}: {e}")
        return False


def send_rejection_notification(user, reason: str = "") -> bool:
    """
    Notify user their account application has been rejected.

    Args:
        user: User model instance
        reason: Reason for rejection (optional)

    Returns:
        True if email was sent successfully, False otherwise
    """
    context = {
        "user": user,
        "user_name": user.name or user.email.split("@")[0],
        "reason": reason,
    }

    try:
        html_message = render_to_string("emails/user_rejected.html", context)
        plain_message = strip_tags(html_message)

        send_mail(
            subject="Regarding your Nxentra account application",
            message=plain_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            html_message=html_message,
            fail_silently=False,
        )
        logger.info(f"Rejection notification sent to {user.email}")
        return True
    except Exception as e:
        logger.error(f"Failed to send rejection notification to {user.email}: {e}")
        return False
