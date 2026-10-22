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


class PublicOtpThrottle(SimpleRateThrottle):
    """Rate-limit public access-code traffic per IP + ticket number.

    Same reasoning as LoginRateThrottle: keying on the ticket as well as the
    source means flooding one ticket cannot exhaust the limit for unrelated
    requesters behind the same NAT.
    """

    scope = "public_otp_request"

    def get_cache_key(self, request, view):
        ticket_no = ""
        if hasattr(request, "data") and isinstance(request.data, dict):
            ticket_no = str(request.data.get("ticket_no", ""))[:40]
        return self.cache_format % {
            "scope": self.scope,
            "ident": f"{self.get_ident(request)}:{ticket_no.lower()}",
        }


class PublicOtpVerifyThrottle(PublicOtpThrottle):
    scope = "public_otp_verify"
