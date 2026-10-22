from django.test import SimpleTestCase

from apps.tickets.services.public_progress import progress_steps, public_stage


class PublicProgressTests(SimpleTestCase):
    def path(self, statuses, current=None):
        transitions = []
        previous = None
        for status in statuses:
            transitions.append((previous, status, None))
            previous = status
        return progress_steps(transitions, current or statuses[-1])

    def test_default_path_shows_every_stage(self):
        rows = self.path(["ASSIGNED"])
        self.assertEqual([row["label"] for row in rows], [
            "Assigned", "Started", "In Progress", "Rectified", "Testing", "Resolved",
        ])
        self.assertEqual([row["state"] for row in rows], ["current", *["upcoming"] * 5])

    def test_started_is_completed_when_work_is_in_progress(self):
        rows = self.path(["ASSIGNED", "IN_PROGRESS"])
        self.assertEqual([row["state"] for row in rows[:3]], ["complete", "complete", "current"])

    def test_internal_holds_keep_the_public_work_stage(self):
        for status in ("PENDING", "ON_HOLD"):
            rows = self.path(["ASSIGNED", "IN_PROGRESS", status])
            self.assertEqual([row["code"] for row in rows if row["state"] == "current"], ["IN_PROGRESS"])
            self.assertNotIn(status, str(rows))
            self.assertEqual(public_stage(status), "IN_PROGRESS")

    def test_rectified_is_completed_when_testing_starts(self):
        rows = self.path(["ASSIGNED", "IN_PROGRESS", "TESTING"])
        self.assertEqual(rows[3]["state"], "complete")
        self.assertEqual(rows[4]["state"], "current")

    def test_tester_return_creates_a_resent_path(self):
        rows = self.path(["ASSIGNED", "IN_PROGRESS", "TESTING", "ASSIGNED"])
        self.assertEqual([row["code"] for row in rows], [
            "RESENT", "STARTED", "IN_PROGRESS", "RECTIFIED", "TESTING", "RESOLVED",
        ])
        self.assertEqual(rows[0]["state"], "current")

    def test_direct_return_to_work_marks_resent_and_started_complete(self):
        rows = self.path(["IN_PROGRESS", "TESTING", "IN_PROGRESS"])
        self.assertEqual([row["state"] for row in rows[:3]], ["complete", "complete", "current"])
        self.assertEqual(rows[0]["code"], "RESENT")

    def test_reopen_without_an_issue_uses_short_testing_path(self):
        rows = self.path(["TESTING", "CLOSED", "REOPENED"])
        self.assertEqual([row["code"] for row in rows], ["REOPEN", "TESTING", "RESOLVED"])
        self.assertEqual(rows[0]["state"], "complete")
        self.assertEqual(rows[1]["state"], "current")
        rows = self.path(["TESTING", "CLOSED", "REOPENED", "CLOSED"])
        self.assertTrue(all(row["state"] == "complete" for row in rows))

    def test_reopened_issue_goes_back_through_development_and_testing(self):
        rows = self.path(["CLOSED", "REOPENED", "ASSIGNED", "IN_PROGRESS", "TESTING"])
        self.assertEqual([row["code"] for row in rows], [
            "REOPEN", "TESTING", "RESENT", "STARTED", "IN_PROGRESS", "RECTIFIED", "TESTING", "RESOLVED",
        ])
        self.assertEqual(rows[-2]["state"], "current")
        self.assertTrue(all(row["state"] == "complete" for row in rows[:-2]))

    def test_repeated_test_failures_do_not_lose_the_reopen_recheck(self):
        rows = self.path(["CLOSED", "REOPENED", "ASSIGNED", "IN_PROGRESS", "TESTING", "ASSIGNED"])
        self.assertEqual(sum(row["code"] == "RESENT" for row in rows), 2)
        self.assertEqual(rows[0]["code"], "REOPEN")
        self.assertEqual(rows[-6]["state"], "current")

    def test_legacy_status_only_ticket_still_has_all_milestones(self):
        rows = progress_steps([], "TESTING")
        self.assertEqual(rows[4]["state"], "current")
        self.assertTrue(all(row["completed_at"] is None for row in rows))

    def test_resolved_and_completed_paths_are_fully_checked(self):
        for status in ("RESOLVED", "COMPLETED", "CLOSED"):
            self.assertTrue(all(row["state"] == "complete" for row in progress_steps([], status)))

    def test_closed_ticket_is_resolved_only_while_confirmation_is_available(self):
        self.assertEqual(public_stage("CLOSED", awaiting_confirmation=True), "RESOLVED")
        self.assertEqual(public_stage("CLOSED"), "CLOSED")

    def test_rejection_does_not_claim_rectification_or_testing_completed(self):
        rows = progress_steps([], "REJECTED")
        self.assertEqual([row["code"] for row in rows], ["CLOSED"])
