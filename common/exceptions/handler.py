"""Central exception handling producing the spec 43 envelope."""

import logging

from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
from django.http import Http404
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.exceptions import PermissionDenied as DRFPermissionDenied
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response
from rest_framework.serializers import as_serializer_error
from rest_framework.views import exception_handler as drf_exception_handler

from common.exceptions.domain import (
    ImmutableRecordError,
    SystemRowProtectedError,
    TransitionNotAllowed,
    WorkflowValidationError,
)

logger = logging.getLogger(__name__)


def _as_list(value):
    if isinstance(value, list):
        return value
    return [value]


def _first_string(value):
    """Unwrap a DRF error value down to a plain string.

    DRF error details are ErrorDetail instances, often nested one list deep
    (a field's errors are always a list, and a raised
    ValidationError({"detail": [...]}) puts a list under "detail" too). str()
    on the list itself prints the Python repr ("[ErrorDetail(string='...',
    code='...')]") instead of the message -- str() on the individual item is
    what actually stringifies to the message text.
    """
    if isinstance(value, (list, tuple)) and value:
        return _first_string(value[0])
    return str(value)


def _extract_message(detail, exc):
    if isinstance(detail, dict):
        if "detail" in detail:
            return _first_string(detail["detail"])
        for value in detail.values():
            items = _as_list(value)
            if items:
                return _first_string(items[0])
    elif isinstance(detail, list) and detail:
        return _first_string(detail[0])
    return _first_string(getattr(exc, "detail", exc)) or "Request failed."


def envelope_exception_handler(exc, context):
    """Translate every exception into {success, message, errors}.

    Spec 43 forbids leaking raw exceptions or stack traces to the client, so an
    unhandled error is logged in full server-side and answered with a generic
    message.
    """
    # ---- DOMAIN EXCEPTIONS ----
    if isinstance(exc, WorkflowValidationError):
        return Response(
            {"success": False, "message": exc.message, "errors": exc.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if isinstance(exc, TransitionNotAllowed):
        return Response(
            {
                "success": False,
                "message": str(exc),
                "errors": {"status": [str(exc)], "allowed_transitions": exc.allowed},
            },
            status=status.HTTP_409_CONFLICT,
        )

    if isinstance(exc, ImmutableRecordError):
        return Response(
            {
                "success": False,
                "message": "This record is immutable and cannot be changed.",
                "errors": {"detail": [str(exc)]},
            },
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    if isinstance(exc, SystemRowProtectedError):
        return Response(
            {
                "success": False,
                "message": str(exc),
                "errors": {"detail": [str(exc)]},
            },
            status=status.HTTP_409_CONFLICT,
        )

    # ---- DJANGO -> DRF TRANSLATION ----
    if isinstance(exc, DjangoValidationError):
        exc = DRFValidationError(as_serializer_error(exc))
    elif isinstance(exc, Http404):
        exc = NotFound()
    elif isinstance(exc, PermissionDenied):
        exc = DRFPermissionDenied()
    elif isinstance(exc, IntegrityError):
        logger.exception("Database integrity error")
        return Response(
            {
                "success": False,
                "message": "This operation conflicts with existing data.",
                "errors": {"detail": ["A record with these values already exists."]},
            },
            status=status.HTTP_409_CONFLICT,
        )

    response = drf_exception_handler(exc, context)

    if response is None:
        logger.exception("Unhandled exception in %s", context.get("view"))
        return Response(
            {
                "success": False,
                "message": "An unexpected error occurred. Please retry.",
                "errors": {"detail": ["Internal server error."]},
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    detail = response.data
    message = _extract_message(detail, exc)
    errors = detail if isinstance(detail, dict) else {"detail": _as_list(detail)}
    response.data = {"success": False, "message": message, "errors": errors}
    return response
