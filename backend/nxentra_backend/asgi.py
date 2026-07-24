import os

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

import accounting.routing

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "nxentra_backend.settings")

# A2: the production entrypoint must run the real settings module (see wsgi.py).
_dsm = os.environ.get("DJANGO_SETTINGS_MODULE")
if _dsm != "nxentra_backend.settings":
    raise RuntimeError(
        f"Refusing to start ASGI: DJANGO_SETTINGS_MODULE={_dsm!r} — must be 'nxentra_backend.settings' in production."
    )

django_app = get_asgi_application()

application = ProtocolTypeRouter(
    {
        "http": django_app,
        "websocket": AuthMiddlewareStack(URLRouter(accounting.routing.websocket_urlpatterns)),
    }
)
