"""Celery entry points for the mail intake pipeline."""

import logging

from celery import shared_task
from django.conf import settings

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    name="apps.mail_intake.tasks.poll_support_mail",
    autoretry_for=(ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=300,
    max_retries=3,
)
def poll_support_mail(self):
    """Poll one bounded batch; the service advisory lock prevents overlap."""
    if not getattr(settings, "MAIL_INTAKE_ENABLED", False):
        logger.info("Mail intake disabled; skipping scheduled poll")
        return {"disabled": True}

    from apps.mail_intake.services.intake_orchestrator import run_intake

    result = run_intake(limit=None).as_dict()
    logger.info("Scheduled mail intake completed: %s", result)
    return result
