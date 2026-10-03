"""Helpers for building enveloped responses directly."""

from rest_framework import status as http_status
from rest_framework.response import Response


def ok(data=None, message="", status=http_status.HTTP_200_OK):
    response = Response(data, status=status)
    response.envelope_message = message
    return response


def created(data=None, message=""):
    return ok(data, message=message, status=http_status.HTTP_201_CREATED)


def fail(errors, message="Request failed.", status=http_status.HTTP_400_BAD_REQUEST):
    response = Response(errors, status=status)
    response.envelope_message = message
    return response


class EnvelopeMessageMixin:
    """Lets a viewset set a per-action success message.

    Usage:  envelope_messages = {"create": "Bug created successfully."}
    """

    envelope_messages: dict = {}

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        if not getattr(response, "envelope_message", ""):
            message = self.envelope_messages.get(getattr(self, "action", ""), "")
            if message:
                response.envelope_message = message
        return response
