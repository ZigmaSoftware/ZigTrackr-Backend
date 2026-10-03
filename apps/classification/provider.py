"""The provider seam that keeps Phase 2 a drop-in (spec 51).

An abstract base class rather than a typing.Protocol on purpose: the provider is
resolved from a dotted settings path, so a mistake should fail loudly the first
time it is instantiated. An ABC raises TypeError on an incomplete subclass;
structural typing would let a half-implemented provider load and then fail
somewhere less obvious.

Phase 1 ships exactly one implementation, RuleClassifier. Phase 2 adds an
AIClassifier beside it and changes one environment variable -- no mail reader
and no bug workflow code is touched, which is the whole point of spec 6.
"""

import abc


class ClassificationProvider(abc.ABC):
    """Turns a MailContext into a ClassificationResult."""

    #: Recorded on the ticket as classification_method.
    name = "UNSPECIFIED"

    @abc.abstractmethod
    def classify(self, context):
        """Return a ClassificationResult for `context`.

        Implementations must be side-effect free: no ticket creation, no status
        changes, no access grants. Classification decides a category and nothing
        else (spec 19, 52).
        """
        raise NotImplementedError
