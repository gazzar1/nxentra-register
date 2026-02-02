# accounts/__init__.py
"""
Accounts app - Authentication and multi-tenancy for Nxentra.

This app provides:
- Company: Tenant/organization model
- User: Custom user model with active_company
- CompanyMembership: User-Company relationship
- NxPermission: Fine-grained permissions
- ActorContext: Authorization context utilities

Multi-tenancy is enforced at every layer through the ActorContext pattern.
"""

default_app_config = "accounts.apps.AccountsConfig"