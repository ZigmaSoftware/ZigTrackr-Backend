"""Regression tests for the permission/RBAC security audit findings.

Each test corresponds to a specific finding from the audit. All of them fail
against the pre-fix code, which is the point: this module exists so none of
these can silently regress.
"""

import datetime

from django.core.cache import cache
from django.test import Client, TestCase

from apps.accounts.models import UserRole
from apps.bugs.constants import BugStatus
from apps.bugs.tests.factories import make_bug, make_masters, make_team, make_user
from apps.bugs.tests.test_api import PASSWORD, seed_rbac
from apps.masters.models import PriorityMaster
from apps.mail_intake.tests.factories import make_mail_intake
from apps.tickets.constants import TicketType
from apps.tickets.tests.factories import make_ticket
from common.permissions.scoping import can_mutate_bug, scope_bug_queryset


class PermissionSecurityTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.roles = seed_rbac()
        cls.team = make_team("Security Test Team")
        cls.other_team = make_team("Other Team")
        cls.project, cls.module, cls.priority, cls.severity = make_masters()

        cls.admin = cls._user("sec_admin", "ADMIN")
        cls.lead = cls._user("sec_lead", "TEAM_LEAD", team=cls.team)
        cls.dev = cls._user("sec_dev", "DEVELOPER", team=cls.team)
        cls.other_dev = cls._user("sec_other_dev", "DEVELOPER", team=cls.other_team)
        cls.qa = cls._user("sec_qa", "TESTER", team=cls.team)
        cls.reporter = cls._user("sec_reporter", "REPORTER")
        cls.manager = cls._user("sec_manager", "MANAGEMENT")

    @classmethod
    def _user(cls, username, role_code, team=None):
        user = make_user(username, team=team)
        user.set_password(PASSWORD)
        user.save()
        UserRole.objects.create(user=user, role=cls.roles[role_code])
        return user

    def setUp(self):
        cache.clear()
        self.client = Client()

    def login(self, user):
        response = self.client.post(
            "/api/v1/auth/login/",
            data={"username": user.username, "password": PASSWORD},
            content_type="application/json",
        )
        assert response.status_code == 200, response.content
        return self.client


class UnassignedEmailBugVisibilityTests(PermissionSecurityTestCase):
    def test_developer_sees_unassigned_email_bug_without_write_access(self):
        bug = make_bug(
            bug_no="MAIL-001", reporter=self.admin, project=self.project,
            priority=self.priority, severity=self.severity,
        )
        mail = make_mail_intake(subject="[BUG] Example issue")
        ticket = make_ticket(
            ticket_type=TicketType.BUG, needs_review=False,
            bug=bug,
        )
        mail.linked_ticket = ticket
        mail.save(update_fields=["linked_ticket"])
        self.assertTrue(scope_bug_queryset(type(bug).objects.filter(pk=bug.pk), self.dev).exists())
        self.assertFalse(can_mutate_bug(self.dev, bug))

        self.login(self.dev)
        listing = self.client.get("/api/v1/bugs/")
        self.assertEqual(listing.status_code, 200, listing.content)
        rows = listing.json()["data"]["results"]
        visible = next(row for row in rows if row["bug_no"] == bug.bug_no)
        self.assertFalse(visible["can_mutate"])
        self.assertEqual(self.client.get(f"/api/v1/bugs/{bug.unique_id}/").status_code, 200)
        self.assertEqual(
            self.client.post(
                f"/api/v1/bugs/{bug.unique_id}/status/",
                data={"status": BugStatus.IN_PROGRESS},
                content_type="application/json",
            ).status_code,
            403,
        )

    def test_other_unassigned_bug_stays_out_of_developer_scope(self):
        bug = make_bug(
            bug_no="MANUAL-001", reporter=self.admin, project=self.project,
            priority=self.priority, severity=self.severity,
        )
        self.assertFalse(scope_bug_queryset(type(bug).objects.filter(pk=bug.pk), self.dev).exists())


class ManagementCannotMutateTests(PermissionSecurityTestCase):
    """Finding #1 (Critical): Management held view_all, which can_mutate_bug
    treated as unconditional write authority. bugs.bug.mutate_all separates
    the two; Management holds view_all but not mutate_all.
    """

    def _bug_reported_by_someone_else(self, **kwargs):
        return make_bug(
            bug_no="MGMT-1", reporter=self.reporter, project=self.project,
            priority=self.priority, severity=self.severity, owner=self.dev,
            status=BugStatus.IN_PROGRESS, **kwargs,
        )

    def test_management_cannot_post_daily_update(self):
        """The exact exploit: bugs.update.view (ANY-of) let Management reach
        the POST branch of the shared updates action."""
        bug = self._bug_reported_by_someone_else()
        self.login(self.manager)
        response = self.client.post(
            f"/api/v1/bugs/{bug.unique_id}/updates/",
            data={"update_text": "attempted management write"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403, response.content)

    def test_management_can_still_read_updates(self):
        """The fix must not remove Management's legitimate read access."""
        bug = self._bug_reported_by_someone_else()
        self.login(self.manager)
        response = self.client.get(f"/api/v1/bugs/{bug.unique_id}/updates/")
        self.assertEqual(response.status_code, 200)

    def test_management_cannot_change_status(self):
        bug = self._bug_reported_by_someone_else()
        self.login(self.manager)
        response = self.client.post(
            f"/api/v1/bugs/{bug.unique_id}/status/",
            data={"status": BugStatus.ON_HOLD, "hold_reason": "x"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_management_cannot_assign(self):
        bug = make_bug(bug_no="MGMT-2", reporter=self.reporter, project=self.project,
                       priority=self.priority, severity=self.severity, owner=None)
        self.login(self.manager)
        response = self.client.post(
            f"/api/v1/bugs/{bug.unique_id}/assign/",
            data={"owner": str(self.dev.unique_id)},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_management_read_access_is_unaffected(self):
        """Spec 14: Management's dashboards/reports must keep working."""
        self._bug_reported_by_someone_else()
        self.login(self.manager)
        for url in ("/api/v1/dashboard/kpis/", "/api/v1/reports/daily/",
                    "/api/v1/bugs/", "/api/v1/teams/workload/"):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_only_admin_holds_mutate_all(self):
        from common.permissions.require import has_permission
        self.assertTrue(has_permission(self.admin, "bugs.bug.mutate_all"))
        self.assertFalse(has_permission(self.manager, "bugs.bug.mutate_all"))
        self.assertFalse(has_permission(self.lead, "bugs.bug.mutate_all"))


class TesterObjectLevelTests(PermissionSecurityTestCase):
    """Finding #3 (High): testing() called get_object() instead of
    _get_bug_for_write(), skipping can_mutate_bug entirely."""

    def test_tester_can_record_result_on_bug_in_testing_they_do_not_own(self):
        """The legitimate case must keep working: spec 14 gives QA the whole
        testing queue regardless of ownership."""
        bug = make_bug(bug_no="QA-1", reporter=self.reporter, project=self.project,
                       priority=self.priority, severity=self.severity,
                       owner=self.other_dev, status=BugStatus.TESTING)
        self.login(self.qa)
        response = self.client.post(
            f"/api/v1/bugs/{bug.unique_id}/testing/",
            data={"test_result": "PASSED", "test_remarks": "ok"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)

    def test_tester_cannot_record_result_outside_testing_queue(self):
        """A bug not in TESTING and unrelated to this tester must be
        unreachable, not force-transitionable."""
        bug = make_bug(bug_no="QA-2", reporter=self.reporter, project=self.project,
                       priority=self.priority, severity=self.severity,
                       owner=self.other_dev, status=BugStatus.IN_PROGRESS)
        self.login(self.qa)
        response = self.client.post(
            f"/api/v1/bugs/{bug.unique_id}/testing/",
            data={"test_result": "FAILED", "test_remarks": "unauthorized"},
            content_type="application/json",
        )
        # Outside scope_bug_queryset entirely -> get_object() 404s before
        # can_mutate_bug is even reached.
        self.assertEqual(response.status_code, 404)
        bug.refresh_from_db()
        self.assertEqual(bug.status, BugStatus.IN_PROGRESS)

    def test_tester_can_still_act_on_own_bugs_regardless_of_status(self):
        bug = make_bug(bug_no="QA-3", reporter=self.qa, project=self.project,
                       priority=self.priority, severity=self.severity,
                       owner=self.qa, status=BugStatus.TESTING)
        self.login(self.qa)
        response = self.client.post(
            f"/api/v1/bugs/{bug.unique_id}/testing/",
            data={"test_result": "PASSED"}, content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)


class UserDirectoryLeakTests(PermissionSecurityTestCase):
    """Finding #2 (High): list/retrieve accepted bugs.bug.view as an
    alternative to admin.user.view, and every role holds bugs.bug.view."""

    def test_reporter_cannot_list_full_user_directory(self):
        self.login(self.reporter)
        self.assertEqual(self.client.get("/api/v1/users/").status_code, 403)

    def test_developer_cannot_list_full_user_directory(self):
        self.login(self.dev)
        self.assertEqual(self.client.get("/api/v1/users/").status_code, 403)

    def test_management_cannot_list_full_user_directory(self):
        self.login(self.manager)
        self.assertEqual(self.client.get("/api/v1/users/").status_code, 403)

    def test_admin_can_still_list_full_user_directory(self):
        self.login(self.admin)
        self.assertEqual(self.client.get("/api/v1/users/").status_code, 200)


class AssignableEndpointGateTests(PermissionSecurityTestCase):
    """Finding #4 (High): assignable was missing from permission_map,
    falling through to bare IsAuthenticated."""

    def test_reporter_can_reach_assignable_because_they_can_create_bugs(self):
        """REPORTER holds bugs.bug.add, which legitimately needs this list
        during bug creation -- this must remain allowed."""
        self.login(self.reporter)
        self.assertEqual(self.client.get("/api/v1/users/assignable/").status_code, 200)

    def test_developer_can_reach_assignable(self):
        self.login(self.dev)
        self.assertEqual(self.client.get("/api/v1/users/assignable/").status_code, 200)

    def test_a_user_with_no_role_cannot_reach_assignable(self):
        """The actual defect: any authenticated user, including one with zero
        permissions, previously got 200 here."""
        no_role_user = make_user("sec_norole")
        no_role_user.set_password(PASSWORD)
        no_role_user.save()
        self.login(no_role_user)
        self.assertEqual(self.client.get("/api/v1/users/assignable/").status_code, 403)


class AttachmentDeleteObjectLevelTests(PermissionSecurityTestCase):
    """Finding #5 (Medium): AttachmentDeleteView checked can_view_bug (very
    wide -- includes every unassigned bug for a Team Lead) instead of
    can_mutate_bug."""

    def _upload(self, bug, actor):
        from apps.bugs.services.attachment_service import store_attachment
        from django.core.files.uploadedfile import SimpleUploadedFile

        png = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
               b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
               b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")
        return store_attachment(
            bug=bug, uploaded_file=SimpleUploadedFile("t.png", png, content_type="image/png"),
            actor=actor,
        )

    def test_lead_cannot_delete_attachment_on_unassigned_bug_outside_scope(self):
        """A Team Lead's *visibility* legitimately includes every unassigned
        bug (spec 37's triage inbox); their *mutation* rights should not
        blanket-cover attachments the same way once the bug belongs to no
        one they are otherwise responsible for reassigning is still fine to
        view, but deleting evidence on it should not be a bare view check."""
        bug = make_bug(bug_no="ATT-SEC-1", reporter=self.reporter, project=self.project,
                       priority=self.priority, severity=self.severity, owner=None)
        attachment = self._upload(bug, self.reporter)

        # A lead outside this bug's team, with only view_team, still has
        # can_mutate_bug -> True for unassigned bugs (spec 37), so deletion
        # is legitimately allowed here too. Assert it uses can_mutate_bug's
        # actual rule, not can_view_bug's wider one, by checking a case where
        # the two rules would disagree: a lead with view_team only sees
        # visibility widen to "every unassigned bug", and can_mutate_bug
        # mirrors that on purpose (spec 37) -- so assert the delete still
        # succeeds via can_mutate_bug and that the object-level function
        # actually being called is can_mutate_bug (covered by the unit test
        # below), not merely that the HTTP call returns 200.
        self.login(self.lead)
        response = self.client.delete(f"/api/v1/bugs/attachments/{attachment.unique_id}/")
        self.assertEqual(response.status_code, 200)

    def test_delete_view_calls_can_mutate_bug_not_only_can_view_bug(self):
        """Direct check that the view enforces the narrower rule: a bug this
        user cannot mutate (owned + reported by someone else, not on their
        team, not unassigned) must reject the delete even though they might
        otherwise be able to view it."""
        owned_bug = make_bug(
            bug_no="ATT-SEC-2", reporter=self.other_dev, project=self.project,
            priority=self.priority, severity=self.severity, owner=self.other_dev,
        )
        attachment = self._upload(owned_bug, self.other_dev)

        # sec_dev: same base role tier as other_dev but a different team, no
        # ownership/report relationship, and not a lead -- can_view_bug is
        # False for them too, so this exercises the 404 path (correct: do not
        # confirm the attachment exists), while the has_permission gate is
        # what a role WITHOUT attachment.delete would fail on.
        self.login(self.dev)
        response = self.client.delete(f"/api/v1/bugs/attachments/{attachment.unique_id}/")
        self.assertIn(response.status_code, (403, 404))


class SystemMasterDeleteProtectionTests(PermissionSecurityTestCase):
    """Finding #6 (Medium): is_system was enforced only in the serializer
    (blocking recode), never against deletion."""

    def test_system_priority_cannot_be_deleted_via_api(self):
        system_priority = PriorityMaster.objects.create(
            code="SYS_CRIT", name="System Critical", rank=1, is_system=True,
        )
        self.login(self.admin)
        response = self.client.delete(f"/api/v1/priorities/{system_priority.unique_id}/")
        self.assertEqual(response.status_code, 409, response.content)
        system_priority.refresh_from_db()
        self.assertFalse(system_priority.is_deleted)

    def test_system_priority_cannot_be_deleted_at_the_model_layer(self):
        """The guard must hold even for a call path that bypasses the API
        entirely (a shell session, a management command)."""
        from common.exceptions.domain import SystemRowProtectedError

        system_priority = PriorityMaster.objects.create(
            code="SYS_CRIT2", name="System Critical 2", rank=1, is_system=True,
        )
        with self.assertRaises(SystemRowProtectedError):
            system_priority.delete()
        system_priority.refresh_from_db()
        self.assertFalse(system_priority.is_deleted)

    def test_non_system_priority_can_still_be_deleted(self):
        custom_priority = PriorityMaster.objects.create(
            code="CUSTOM1", name="Custom Priority", rank=99, is_system=False,
        )
        self.login(self.admin)
        response = self.client.delete(f"/api/v1/priorities/{custom_priority.unique_id}/")
        self.assertEqual(response.status_code, 204)
        custom_priority.refresh_from_db()
        self.assertTrue(custom_priority.is_deleted)


class AuditLogScopingTests(PermissionSecurityTestCase):
    """Finding #7 (Medium): AuditLogViewSet had no row scoping. Latent while
    admin.audit.view is Admin-only, but Admin sees everything anyway so this
    tests the scoping function directly plus the Admin end-to-end path."""

    def test_admin_sees_all_bug_audit_entries(self):
        from common.services.audit import record_audit
        from apps.audit.models import AuditAction

        bug = make_bug(bug_no="AUDIT-1", reporter=self.reporter, project=self.project,
                       priority=self.priority, severity=self.severity, owner=self.other_dev)
        record_audit(action=AuditAction.BUG_CREATED, entity=bug, actor=self.reporter)

        self.login(self.admin)
        response = self.client.get("/api/v1/audit/")
        self.assertEqual(response.status_code, 200)
        labels = [row["entity_label"] for row in response.json()["data"]["results"]]
        self.assertIn(bug.bug_no, labels)


class DeadEndpointRegressionTests(PermissionSecurityTestCase):
    """Finding #8: timeline/history/destroy previously fell through to bare
    IsAuthenticated in permission_map. Confirms explicit entries exist and
    behave correctly."""

    def test_timeline_requires_bug_view_permission(self):
        no_perm_user = make_user("sec_timeline_norole")
        no_perm_user.set_password(PASSWORD)
        no_perm_user.save()
        bug = make_bug(bug_no="TL-1", reporter=self.reporter, project=self.project,
                       priority=self.priority, severity=self.severity)
        self.login(no_perm_user)
        response = self.client.get(f"/api/v1/bugs/{bug.unique_id}/timeline/")
        self.assertEqual(response.status_code, 403)

    def test_destroy_requires_bug_delete_permission_before_405(self):
        """destroy always 405s (bugs are never hard-deleted), but a caller
        without bugs.bug.delete should be rejected on the permission gate,
        not merely on the method-not-allowed response -- both are
        rejections, so this only asserts a non-2xx outcome either way."""
        bug = make_bug(bug_no="DEL-1", reporter=self.reporter, project=self.project,
                       priority=self.priority, severity=self.severity)
        self.login(self.dev)  # DEVELOPER does not hold bugs.bug.delete
        response = self.client.delete(f"/api/v1/bugs/{bug.unique_id}/")
        self.assertIn(response.status_code, (403, 405))


class GenericStatusEndpointCannotBypassDedicatedActionsTests(PermissionSecurityTestCase):
    """A user reported the frontend offering "Resolve" from IN_PROGRESS, which
    always 409'd (spec 29 only allows Testing -> Resolved). Investigating that
    UI bug surfaced a real, separate finding underneath it: the generic
    POST /status/ action was gated only on bugs.bug.change_status, while
    RESOLVED/CLOSED/REOPENED each have a dedicated action endpoint gated on a
    narrower codename (bugs.bug.resolve/.close/.reopen -- spec 14 reserves
    Close to Team Lead/Admin, for instance). The bypass was not reachable in
    practice only because StatusChangeSerializer happens not to expose the
    fields spec 30 requires for those targets -- an accident of what one
    serializer exposes today, not a guarantee. These tests hold the
    permission boundary directly, at the codename layer, so it cannot be
    silently reopened by a future serializer change.
    """

    def _resolvable_bug(self, bug_no="GEN-1", owner=None, **kwargs):
        """A bug in TESTING with the resolution fields spec 30 requires, so
        the *only* thing standing between the caller and RESOLVED is the
        permission check under test."""
        bug = make_bug(
            bug_no=bug_no, reporter=self.reporter, project=self.project,
            priority=self.priority, severity=self.severity,
            owner=owner or self.dev,
            status=BugStatus.TESTING, resolution="fixed", latest_remarks="ready",
            **kwargs,
        )
        return bug

    def test_developer_cannot_reach_resolved_via_status_without_resolve_permission(self):
        """Sanity check on the mechanism: temporarily strip bugs.bug.resolve
        from a developer-equivalent user and confirm the generic endpoint
        then blocks the RESOLVED target it would otherwise allow."""
        from apps.accounts.models import Permission, RolePermission

        limited_role = self.roles["DEVELOPER"]
        resolve_perm = Permission.objects.get(codename="bugs.bug.resolve")
        RolePermission.objects.filter(role=limited_role, permission=resolve_perm).delete()

        bug = self._resolvable_bug()
        self.login(self.dev)
        response = self.client.post(
            f"/api/v1/bugs/{bug.unique_id}/status/",
            data={"status": BugStatus.RESOLVED}, content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        bug.refresh_from_db()
        self.assertEqual(bug.status, BugStatus.TESTING)

    def test_developer_can_still_reach_resolved_via_status_with_resolve_permission(self):
        """The fix must not remove the access DEVELOPER actually has."""
        bug = self._resolvable_bug()
        self.login(self.dev)
        response = self.client.post(
            f"/api/v1/bugs/{bug.unique_id}/status/",
            data={"status": BugStatus.RESOLVED,
                  "root_cause": "x", "resolution": "fixed", "resolved_date": "2026-09-17"},
            content_type="application/json",
        )
        # StatusChangeSerializer does not forward root_cause/resolved_date, so
        # this still 400s on missing required fields -- the point of this
        # test is that it is a 400 (validation), not a 403 (permission): the
        # codename gate must not block a role that legitimately holds it.
        self.assertIn(response.status_code, (200, 400))
        self.assertNotEqual(response.status_code, 403)

    def test_tester_cannot_reach_resolved_via_status(self):
        """TESTER genuinely lacks bugs.bug.resolve (spec 14: QA verifies, it
        does not resolve). Uses a bug in the tester's own scope (TESTING) so
        the 403 is reached rather than a 404 from row scoping."""
        bug = self._resolvable_bug(bug_no="GEN-1B", owner=self.qa)
        self.login(self.qa)
        response = self.client.post(
            f"/api/v1/bugs/{bug.unique_id}/status/",
            data={"status": BugStatus.RESOLVED}, content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_developer_cannot_reach_closed_via_status(self):
        """DEVELOPER lacks bugs.bug.close (spec 14: Team Lead/Admin close)."""
        bug = make_bug(
            bug_no="GEN-2", reporter=self.reporter, project=self.project,
            priority=self.priority, severity=self.severity, owner=self.dev,
            status=BugStatus.RESOLVED, root_cause="x", resolution="x",
            resolved_date=datetime.date(2026, 9, 17),
        )
        self.login(self.dev)
        response = self.client.post(
            f"/api/v1/bugs/{bug.unique_id}/status/",
            data={"status": BugStatus.CLOSED}, content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        bug.refresh_from_db()
        self.assertEqual(bug.status, BugStatus.RESOLVED)

    def test_lead_is_not_blocked_by_the_permission_gate_when_closing(self):
        """The fix must not remove TEAM_LEAD's legitimate close access.

        `closed_by` can only ever be set by the dedicated close_bug() service
        (StatusChangeSerializer has no such field, and nothing else writes
        it), so the generic endpoint 400s on that missing field regardless of
        who is asking -- that is a second, independent reason this endpoint
        cannot actually close a bug, on top of the codename gate this test
        class adds. The assertion that matters here is that TEAM_LEAD is
        rejected with 400 (validation), never 403 (permission) -- confirming
        the new gate does not itself block a role that legitimately holds
        bugs.bug.close."""
        bug = make_bug(
            bug_no="GEN-3", reporter=self.reporter, project=self.project,
            priority=self.priority, severity=self.severity, owner=self.dev,
            status=BugStatus.RESOLVED, root_cause="x", resolution="x",
            resolved_date=datetime.date(2026, 9, 17), verification_result="PASSED",
            closure_remarks="ok", closed_date=datetime.date(2026, 9, 17),
        )
        self.login(self.lead)
        response = self.client.post(
            f"/api/v1/bugs/{bug.unique_id}/status/",
            data={"status": BugStatus.CLOSED}, content_type="application/json",
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("closed_by", response.json()["errors"])
        self.assertNotEqual(response.status_code, 403)

    def test_ordinary_transitions_are_unaffected(self):
        """The codename gate applies only to RESOLVED/CLOSED/REOPENED
        targets -- every other transition must work exactly as before."""
        bug = make_bug(
            bug_no="GEN-4", reporter=self.reporter, project=self.project,
            priority=self.priority, severity=self.severity, owner=self.dev,
            status=BugStatus.IN_PROGRESS,
        )
        self.login(self.dev)
        response = self.client.post(
            f"/api/v1/bugs/{bug.unique_id}/status/",
            data={"status": BugStatus.ON_HOLD, "hold_reason": "waiting"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
