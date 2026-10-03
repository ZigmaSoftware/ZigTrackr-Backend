"""The façade the mail intake pipeline calls (spec 6, 51).

Intake depends on this one function and on the ClassificationResult DTO, and on
nothing else in this app. That is what lets Phase 2 add an AIClassifier by
changing a single environment variable, with no edit to the mail reader or to
any bug workflow code.
"""

import logging

from django.conf import settings
from django.utils.module_loading import import_string

from apps.classification.config import ClassificationConfig
from apps.classification.dto import ClassificationResult
from apps.classification.services.module_mapper import ModuleMapper

logger = logging.getLogger(__name__)

DEFAULT_PROVIDER = "apps.classification.services.rule_classifier.RuleClassifier"


def get_provider(config=None):
    """Instantiate the configured classification provider.

    Resolved from a dotted path so Phase 2 is a settings change. An unknown or
    broken path raises here, at first use, rather than silently degrading.
    """
    path = getattr(settings, "CLASSIFICATION_PROVIDER", DEFAULT_PROVIDER)
    provider_class = import_string(path)
    return provider_class(config=config or ClassificationConfig.from_settings())


def classify_mail(context, *, config=None, provider=None, mapper=None):
    """Classify a message and map it to a project/module.

    Returns a ClassificationResult. Type classification and module mapping are
    independent: an email can be a confident BUG whose module is ambiguous, and
    that still needs a human, because a Bug cannot be created without a project.
    """
    config = config or ClassificationConfig.from_settings()
    provider = provider or get_provider(config)
    mapper = mapper or ModuleMapper(config=config)

    result = provider.classify(context)

    mapping = mapper.map(context.normalized_subject, context.normalized_body)

    needs_review = result.needs_review or mapping.is_ambiguous
    reason = result.review_reason
    if mapping.is_ambiguous:
        reason = f"{reason} {mapping.reason}".strip()

    return ClassificationResult(
        ticket_type=result.ticket_type,
        classification_method=result.classification_method,
        classification_score=result.classification_score,
        matched_rules=result.matched_rules,
        all_scores=result.all_scores,
        project_unique_id=mapping.project_unique_id,
        module_unique_id=mapping.module_unique_id,
        submodule_unique_id=mapping.submodule_unique_id,
        needs_review=needs_review,
        review_reason=reason,
        provider_name=result.provider_name,
    )
