"""Classification vocabulary (spec 14, 16)."""

from django.db import models


class RuleType(models.TextChoices):
    SUBJECT_PREFIX = "SUBJECT_PREFIX", "Subject Prefix"
    EXACT_PHRASE = "EXACT_PHRASE", "Exact Phrase"
    KEYWORD = "KEYWORD", "Keyword"
    FUZZY_KEYWORD = "FUZZY_KEYWORD", "Fuzzy Keyword"


class MatchField(models.TextChoices):
    SUBJECT = "SUBJECT", "Subject"
    BODY = "BODY", "Body"


# The ticket types the engine can decide between. UNKNOWN is an outcome, never a
# rule target, so it is deliberately absent here.
CLASSIFIABLE_TICKET_TYPES = ("BUG", "SERVICE_REQUEST", "ACCESS_REQUEST")
