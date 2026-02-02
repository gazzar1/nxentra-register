from django.contrib import admin
from django.urls import include, path
from django.conf import settings
from django.conf.urls.static import static

from ops.urls import metrics_patterns

urlpatterns = [
    # Operations endpoints (no auth required)
    path("_health/", include("ops.urls")),
    path("_metrics/", include(metrics_patterns)),

    # Admin and API
    path("admin/", admin.site.urls),
    path("api/", include("accounts.urls")),
    path("api/accounting/", include("accounting.urls")),
    path("api/reports/", include("projections.urls")),
    path("api/edim/", include("edim.urls")),
    path("api/events/", include("events.urls")),
    path("api-auth/", include("rest_framework.urls")),
]

# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
