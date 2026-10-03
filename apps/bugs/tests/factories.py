"""Test helpers for building bug fixtures."""

import datetime

from django.contrib.auth import get_user_model

from apps.bugs.constants import BugStatus
from apps.bugs.models import Bug
from apps.masters.models import (
    ModuleMaster,
    PriorityMaster,
    ProjectMaster,
    SeverityMaster,
    TeamMaster,
)


def make_user(username="tester", **kwargs):
    User = get_user_model()
    defaults = {"full_name": username.title(), "email": f"{username}@example.com"}
    defaults.update(kwargs)
    user = User.objects.create(username=username, **defaults)
    user.set_password("pw-not-used")
    user.save()
    return user


def make_team(name="Team A"):
    return TeamMaster.objects.create(name=name)


def make_masters():
    project = ProjectMaster.objects.create(code="TST", name="Test Project")
    module = ModuleMaster.objects.create(project=project, name="Sales")
    priority = PriorityMaster.objects.create(code="HIGH", name="High", rank=20)
    severity = SeverityMaster.objects.create(code="MAJOR", name="Major", rank=30)
    return project, module, priority, severity


def make_bug(*, bug_no, reporter, project, priority, severity, module=None,
             status=BugStatus.NEW, reported_date=None, expected_closure_date=None,
             closed_date=None, owner=None, latest_update_date=None, **kwargs):
    return Bug.objects.create(
        bug_no=bug_no,
        project=project,
        module=module,
        title=kwargs.pop("title", f"Issue {bug_no}"),
        description=kwargs.pop("description", "Description"),
        reported_by=reporter,
        reported_date=reported_date or datetime.date(2026, 9, 14),
        priority=priority,
        severity=severity,
        status=status,
        owner=owner,
        expected_closure_date=expected_closure_date,
        closed_date=closed_date,
        latest_update_date=latest_update_date,
        **kwargs,
    )
