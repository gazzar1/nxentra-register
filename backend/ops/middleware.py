"""
Content Security Policy middleware.

Reads CSP_* settings from Django settings and adds the Content-Security-Policy
header to every response.  Only active when SECURE_CSP_ENABLED is True
(production — see settings.py).
"""

from django.conf import settings


class ContentSecurityPolicyMiddleware:
    """Add Content-Security-Policy header to all responses."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.csp_header = self._build_header()

    def __call__(self, request):
        response = self.get_response(request)
        if self.csp_header:
            response["Content-Security-Policy"] = self.csp_header
        return response

    @staticmethod
    def _build_header():
        if not getattr(settings, "SECURE_CSP_ENABLED", False):
            return ""

        directives = []
        mapping = {
            "default-src": "CSP_DEFAULT_SRC",
            "script-src": "CSP_SCRIPT_SRC",
            "style-src": "CSP_STYLE_SRC",
            "img-src": "CSP_IMG_SRC",
            "font-src": "CSP_FONT_SRC",
            "connect-src": "CSP_CONNECT_SRC",
            "frame-ancestors": "CSP_FRAME_ANCESTORS",
        }
        for directive, setting_name in mapping.items():
            values = getattr(settings, setting_name, None)
            if values:
                directives.append(f"{directive} {' '.join(values)}")

        return "; ".join(directives)
