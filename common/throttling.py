"""Throttling for authentication endpoints (spec 20.5, 46)."""

from rest_framework.throttling import SimpleRateThrottle


class LoginRateThrottle(SimpleRateThrottle):
    """Rate-limit login attempts per IP + attempted username.

    Keying on both means one attacker cannot lock out a legitimate user by
    hammering their username from elsewhere, while still slowing a password
    spray from a single source.
    """

    scope = "login"

    def get_cache_key(self, request, view):
        username = ""
        if hasattr(request, "data") and isinstance(request.data, dict):
            username = str(request.data.get("username", ""))[:150]
        return self.cache_format % {
            "scope": self.scope,
            "ident": f"{self.get_ident(request)}:{username.lower()}",
        }
