"""Domain exceptions raised by the service layer."""


class DomainError(Exception):
    """Base for business-rule violations."""


class ImmutableRecordError(DomainError):
    """Raised on any attempt to modify or delete a history row."""


class TransitionNotAllowed(DomainError):
    """Raised when a status transition is not permitted by the state machine."""

    def __init__(self, message, from_status=None, to_status=None, allowed=None):
        super().__init__(message)
        self.from_status = from_status
        self.to_status = to_status
        self.allowed = list(allowed or [])


class WorkflowValidationError(DomainError):
    """Raised when required fields for a target status are missing.

    Carries a field -> [messages] dict so every missing field is reported at
    once. Closing a bug requires six fields (spec 30); surfacing them one at a
    time would make closure a guessing game.
    """

    def __init__(self, errors, message="Validation failed."):
        super().__init__(message)
        self.errors = errors
        self.message = message


class SystemRowProtectedError(DomainError):
    """Raised when code tries to delete a seeded/system master row.

    Enforced on CodedMaster.soft_delete() itself (common/models/base.py), not
    only in a serializer, so the rule holds for every call path -- API,
    management command, or future bulk operation.
    """
