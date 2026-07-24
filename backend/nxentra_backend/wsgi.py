import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "nxentra_backend.settings")

# A2: the production entrypoint must run the real settings module. Refuse to
# serve if it was overridden to the test-settings module (or anything else),
# which would disable RLS, event validation and the security-hardening block.
_dsm = os.environ.get("DJANGO_SETTINGS_MODULE")
if _dsm != "nxentra_backend.settings":
    raise RuntimeError(
        f"Refusing to start WSGI: DJANGO_SETTINGS_MODULE={_dsm!r} — must be 'nxentra_backend.settings' in production."
    )

application = get_wsgi_application()
