"""Declarative permission catalog (spec 14, 16).

This module is the single source of truth for what permissions exist. The
`seed_permissions` command materialises it into the `permissions` table, which
the Permission Management screen (spec 16) then reads. Adding a permission means
adding a line here and re-running the seed.

Codename format: "<module>.<screen>.<action>".
"""

# (codename, name, module, screen_code, screen_name, action)
PERMISSION_CATALOG = [
    # ---- DASHBOARD ----
    ("dashboard.dashboard.view", "View Dashboard", "dashboard", "dashboard", "Dashboard", "view"),
    ("dashboard.dashboard.view_team", "View Team Dashboard", "dashboard", "dashboard", "Dashboard", "view"),

    # ---- BUGS ----
    ("bugs.bug.view", "View Bugs", "bugs", "bug", "Bug Management", "view"),
    ("bugs.bug.view_all", "View All Bugs", "bugs", "bug", "Bug Management", "view"),
    ("bugs.bug.view_team", "View Team Bugs", "bugs", "bug", "Bug Management", "view"),
    ("bugs.bug.view_unassigned_email", "View Unassigned Email Bugs", "bugs", "bug", "Bug Management", "view"),
    # Visibility and write authority are deliberately separate codenames.
    # view_all lets Management see every bug for reporting; mutate_all is what
    # actually authorizes writing to a bug you don't own. Collapsing the two
    # (as an earlier version of this catalog did) let a read-only role write
    # to any bug via any endpoint whose codename gate happened to admit them.
    ("bugs.bug.mutate_all", "Modify Any Bug", "bugs", "bug", "Bug Management", "use"),
    ("bugs.bug.add", "Create Bug", "bugs", "bug", "Bug Management", "add"),
    ("bugs.bug.edit", "Edit Bug", "bugs", "bug", "Bug Management", "edit"),
    ("bugs.bug.delete", "Delete Bug", "bugs", "bug", "Bug Management", "delete"),
    ("bugs.bug.assign", "Assign Bug", "bugs", "bug", "Bug Management", "use"),
    ("bugs.bug.change_status", "Change Bug Status", "bugs", "bug", "Bug Management", "use"),
    # NOTE: change_priority and send_to_testing are catalogued and shown on the
    # Permission Management matrix for completeness against the spec's role
    # descriptions, but neither is independently enforced today. Priority is
    # writable via plain bugs.bug.edit (PATCH /bugs/<id>/), and moving a bug to
    # TESTING is writable via bugs.bug.change_status (POST /bugs/<id>/status/)
    # -- the generic endpoints do not special-case the target field/status to
    # require a second, narrower codename. A role holding edit/change_status
    # but not these two therefore has the access anyway. Do not read the
    # matrix as proof of finer-grained enforcement than that.
    ("bugs.bug.change_priority", "Change Priority/Severity", "bugs", "bug", "Bug Management", "use"),
    ("bugs.bug.send_to_testing", "Send Bug to Testing", "bugs", "bug", "Bug Management", "use"),
    ("bugs.bug.test", "Test / Verify Bug", "bugs", "bug", "Bug Management", "use"),
    ("bugs.bug.resolve", "Resolve Bug", "bugs", "bug", "Bug Management", "use"),
    ("bugs.bug.close", "Close Bug", "bugs", "bug", "Bug Management", "use"),
    ("bugs.bug.reopen", "Reopen Bug", "bugs", "bug", "Bug Management", "use"),

    # ---- DAILY UPDATES ----
    ("bugs.update.view", "View Daily Updates", "bugs", "update", "Daily Updates", "view"),
    ("bugs.update.add", "Add Daily Update", "bugs", "update", "Daily Updates", "add"),

    # ---- ATTACHMENTS ----
    ("bugs.attachment.view", "View Attachments", "bugs", "attachment", "Attachments", "view"),
    ("bugs.attachment.add", "Upload Attachment", "bugs", "attachment", "Attachments", "add"),
    ("bugs.attachment.delete", "Delete Attachment", "bugs", "attachment", "Attachments", "delete"),

    # ---- TEAM ----
    ("teams.workload.view", "View Developer Workload", "teams", "workload", "Team Management", "view"),
    ("teams.assignment.use", "Bug Assignment Board", "teams", "assignment", "Team Management", "use"),

    # ---- REPORTS ----
    ("reports.report.view", "View Reports", "reports", "report", "Reports", "view"),
    ("reports.report.export", "Export Reports", "reports", "report", "Reports", "use"),

    # ---- MASTERS ----
    ("masters.master.view", "View Masters", "masters", "master", "Masters", "view"),
    ("masters.master.add", "Create Master", "masters", "master", "Masters", "add"),
    ("masters.master.edit", "Edit Master", "masters", "master", "Masters", "edit"),
    ("masters.master.delete", "Delete Master", "masters", "master", "Masters", "delete"),

    # ---- ADMINISTRATION ----
    ("admin.user.view", "View Users", "administration", "user", "User Management", "view"),
    ("admin.user.add", "Create User", "administration", "user", "User Management", "add"),
    ("admin.user.edit", "Edit User", "administration", "user", "User Management", "edit"),
    ("admin.user.delete", "Deactivate User", "administration", "user", "User Management", "delete"),
    ("admin.role.view", "View Roles", "administration", "role", "Role Management", "view"),
    ("admin.role.manage", "Manage Roles", "administration", "role", "Role Management", "edit"),
    ("admin.permission.view", "View Permissions", "administration", "permission", "Permission Management", "view"),
    ("admin.permission.manage", "Manage Permissions", "administration", "permission", "Permission Management", "edit"),
    ("admin.audit.view", "View Audit Log", "administration", "audit", "Audit Log", "view"),

    # ---- MAIL INTAKE (spec 46) ----
    # Note the codenames below rename spec 46's `access_request.approve` family to
    # `access.request.*`. The catalog format is "<module>.<screen>.<action>" and the
    # Permission Management matrix groups by module and screen, so a two-segment
    # codename would render in a broken group.
    ("mail_intake.mail.view", "View Mail Intake", "mail_intake", "mail", "Mail Intake", "view"),
    ("mail_intake.mail.view_all", "View All Intake Mail", "mail_intake", "mail", "Mail Intake", "view"),
    ("mail_intake.mail.reprocess", "Reprocess Mail", "mail_intake", "mail", "Mail Intake", "use"),
    ("mail_intake.mail.confirm_classification", "Confirm Mail Classification", "mail_intake", "mail", "Mail Intake", "use"),
    ("mail_intake.mail.process_now", "Trigger Mail Fetch", "mail_intake", "mail", "Mail Intake", "use"),
    # Separate from rules.view on purpose: rule patterns and scores are business
    # configuration (spec 39), so "may see why this mail scored 82" is a different
    # question from "may browse the rule master".
    ("mail_intake.mail.view_diagnostics", "View Classification Diagnostics", "mail_intake", "mail", "Mail Intake", "view"),
    ("mail_intake.mail.ignore", "Ignore / Discard Mail", "mail_intake", "mail", "Mail Intake", "use"),

    # ---- CLASSIFICATION RULES ----
    ("mail_intake.rules.view", "View Classification Rules", "mail_intake", "rules", "Classification Rules", "view"),
    ("mail_intake.rules.manage", "Manage Classification Rules", "mail_intake", "rules", "Classification Rules", "edit"),

    # ---- TICKETS ----
    ("tickets.ticket.view", "View Tickets", "tickets", "ticket", "Ticket Management", "view"),
    ("tickets.ticket.view_all", "View All Tickets", "tickets", "ticket", "Ticket Management", "view"),
    # Same split as bugs.bug.mutate_all, for the same reason: Management holds
    # view_all for reporting and must not be able to write to tickets it does not own.
    ("tickets.ticket.mutate_all", "Modify Any Ticket", "tickets", "ticket", "Ticket Management", "use"),
    ("tickets.ticket.create", "Create Ticket", "tickets", "ticket", "Ticket Management", "add"),
    ("tickets.ticket.update", "Update Ticket", "tickets", "ticket", "Ticket Management", "edit"),
    ("tickets.ticket.delete", "Delete Ticket", "tickets", "ticket", "Ticket Management", "delete"),
    ("tickets.ticket.classify", "Classify / Correct Ticket", "tickets", "ticket", "Ticket Management", "use"),
    ("tickets.ticket.assign", "Assign Ticket", "tickets", "ticket", "Ticket Management", "use"),
    ("tickets.ticket.reassign", "Reassign Ticket", "tickets", "ticket", "Ticket Management", "use"),
    ("tickets.ticket.add_update", "Add Ticket Update", "tickets", "ticket", "Ticket Management", "add"),
    ("tickets.ticket.verify_close", "Verify and Close Ticket", "tickets", "ticket", "Ticket Management", "use"),

    # ---- ACCESS REQUESTS (spec 29) ----
    # approve and implement are deliberately distinct and held by different roles:
    # the person who authorises a permission change is not the person who makes it.
    # Ordering (approve before implement) is a state-machine rule the service layer
    # enforces; a codename cannot express it.
    ("access.request.view", "View Access Requests", "access", "request", "Access Requests", "view"),
    ("access.request.approve", "Approve Access Request", "access", "request", "Access Requests", "use"),
    ("access.request.reject", "Reject Access Request", "access", "request", "Access Requests", "use"),
    ("access.request.implement", "Implement Access Change", "access", "request", "Access Requests", "use"),
]

ALL_CODENAMES = [row[0] for row in PERMISSION_CATALOG]

# ---- ROLE DEFINITIONS (spec 14) ----
ROLE_ADMIN = "ADMIN"
ROLE_TEAM_LEAD = "TEAM_LEAD"
ROLE_DEVELOPER = "DEVELOPER"
ROLE_TESTER = "TESTER"
ROLE_REPORTER = "REPORTER"
ROLE_MANAGEMENT = "MANAGEMENT"

ROLE_DEFINITIONS = [
    (ROLE_ADMIN, "Admin", 10, "Full system access."),
    (ROLE_TEAM_LEAD, "Team Lead", 20, "Assigns work, reviews updates, verifies and closes bugs."),
    (ROLE_DEVELOPER, "Developer", 30, "Works assigned bugs and posts daily updates."),
    (ROLE_TESTER, "Tester / QA", 40, "Verifies fixes and records test results."),
    (ROLE_REPORTER, "Reporter / User", 50, "Reports bugs and tracks their own submissions."),
    (ROLE_MANAGEMENT, "Management", 60, "Read-only dashboards and reports."),
]

_COMMON_SELF_SERVICE = [
    "dashboard.dashboard.view",
    "bugs.bug.view",
    "bugs.update.view",
    "bugs.attachment.view",
    "bugs.attachment.add",
    # Every role that can view or create a bug needs to read master data --
    # the Create Bug form's Project/Module/Submodule/Priority/Severity/Root
    # Cause Type/Department/Site dropdowns, and the bug list's filter panel,
    # all call these endpoints regardless of role. Without this, Developer,
    # Tester and Reporter could see the Create Bug button but every dropdown
    # on the form would 403 -- discovered via an end-to-end workflow test
    # logged in as each role, not by inspection.
    "masters.master.view",
]

ROLE_PERMISSIONS = {
    ROLE_ADMIN: list(ALL_CODENAMES),

    ROLE_TEAM_LEAD: [
        "dashboard.dashboard.view", "dashboard.dashboard.view_team",
        "bugs.bug.view", "bugs.bug.view_team", "bugs.bug.add", "bugs.bug.edit",
        "bugs.bug.assign", "bugs.bug.change_status", "bugs.bug.change_priority",
        "bugs.bug.send_to_testing", "bugs.bug.test", "bugs.bug.resolve",
        "bugs.bug.close", "bugs.bug.reopen",
        "bugs.update.view", "bugs.update.add",
        "bugs.attachment.view", "bugs.attachment.add", "bugs.attachment.delete",
        "teams.workload.view", "teams.assignment.use",
        "reports.report.view", "reports.report.export",
        "masters.master.view", "masters.master.add", "masters.master.edit",
        # Mail intake: a lead triages the queue and confirms classifications, but
        # cannot trigger a fetch or edit the rule master -- those are Admin knobs.
        "mail_intake.mail.view", "mail_intake.mail.reprocess",
        "mail_intake.mail.confirm_classification", "mail_intake.mail.view_diagnostics",
        "mail_intake.mail.ignore",
        # view but not manage: leads need to understand why routing happened.
        "mail_intake.rules.view",
        "tickets.ticket.view", "tickets.ticket.classify",
        "tickets.ticket.create", "tickets.ticket.assign", "tickets.ticket.reassign",
        "tickets.ticket.add_update",
        "tickets.ticket.verify_close",
        # Approves and rejects, but does NOT implement -- separation of duties.
        "access.request.view", "access.request.approve", "access.request.reject",
    ],

    ROLE_DEVELOPER: _COMMON_SELF_SERVICE + [
        "bugs.bug.add", "bugs.bug.edit", "bugs.bug.change_status",
        "bugs.bug.view_unassigned_email",
        "bugs.bug.send_to_testing", "bugs.bug.resolve",
        "bugs.update.add",
        "reports.report.view",
        # Sees tickets assigned to them and can comment. No rule-master access and
        # no approval authority (spec 55).
        "tickets.ticket.view", "tickets.ticket.create", "tickets.ticket.add_update",
        # Assigned developers implement access only after a lead approves it.
        "access.request.implement",
    ],

    ROLE_TESTER: _COMMON_SELF_SERVICE + [
        "bugs.bug.change_status", "bugs.bug.test",
        "bugs.update.add",
        "reports.report.view",
        # Explicitly no access.request.approve: spec 55 requires a tester be unable
        # to approve an access request.
        "tickets.ticket.view", "tickets.ticket.create", "tickets.ticket.add_update",
        "tickets.ticket.verify_close",
    ],

    ROLE_REPORTER: _COMMON_SELF_SERVICE + [
        "bugs.bug.add",
        "bugs.update.add",
        # Reporters see their own tickets only; scoping decides which rows.
        # Deliberately no mail-intake access -- other people's mail is not theirs.
        "tickets.ticket.view", "tickets.ticket.create",
    ],

    # Read-only by design (spec 14): view_all with no write codename at all.
    ROLE_MANAGEMENT: [
        "dashboard.dashboard.view", "dashboard.dashboard.view_team",
        "bugs.bug.view", "bugs.bug.view_all",
        "bugs.update.view", "bugs.attachment.view",
        "reports.report.view", "reports.report.export",
        "teams.workload.view",
        "masters.master.view",
        # Read-only here too: view and view_all, and not one mutating codename.
        # No reprocess, no confirm, no approve. This is the row to re-check when
        # adding anything to Management.
        "mail_intake.mail.view", "mail_intake.mail.view_all",
        "tickets.ticket.view", "tickets.ticket.view_all",
        "access.request.view",
    ],
}
