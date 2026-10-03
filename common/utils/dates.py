"""Date helpers.

Every aggregate in a single request should share one notion of "today",
otherwise a request spanning midnight can report inconsistent numbers across
its own KPI cards.
"""

from django.utils import timezone


def local_today():
    return timezone.localdate()


def local_now():
    return timezone.now()
