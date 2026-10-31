import base64
import hmac

from django.conf import settings
from django.http import HttpResponse


class BasicAuthMiddleware:
    """Optional HTTP Basic auth. Active only when WEBCTL_AUTH_USER is set.
    Enable this before any internet exposure (DuckDNS/Caddy/tunnel)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = settings.WEBCTL_AUTH_USER
        if not user:
            return self.get_response(request)
        header = request.headers.get("Authorization", "")
        if header.startswith("Basic "):
            try:
                decoded = base64.b64decode(header[6:]).decode("utf-8")
                got_user, _, got_pass = decoded.partition(":")
                if (hmac.compare_digest(got_user, user)
                        and hmac.compare_digest(got_pass, settings.WEBCTL_AUTH_PASS)):
                    return self.get_response(request)
            except (ValueError, UnicodeDecodeError):
                pass
        return HttpResponse("Authentication required", status=401,
                            headers={"WWW-Authenticate": 'Basic realm="wheeltec-robot"'})
