"""Sidebar ticket screens and their action bundles (visibility is not ownership)."""

WORK_ACTIONS = ("tickets.ticket.view", "tickets.ticket.add_update", "tickets.ticket.verify_close",
                "masters.master.view", "bugs.attachment.view")
BUG_ACTIONS = ("bugs.bug.view", "bugs.bug.edit", "bugs.bug.change_status", "bugs.bug.test",
               "bugs.bug.resolve", "bugs.bug.close", "bugs.bug.reopen")
ACCESS_ACTIONS = ("access.request.view", "access.request.approve", "access.request.reject",
                  "access.request.implement")

# key, display name, group, prerequisite used ONLY when upgrading old grants,
# actions granted when an administrator explicitly selects the screen.
TICKET_SUBMODULES = (
    ("create", "Create Ticket", "ticket_creation", "tickets.ticket.create", ("tickets.ticket.create", "masters.master.view")),
    ("unassigned", "Unassigned Tickets", "ticket_creation", "tickets.ticket.view",
     ("tickets.ticket.view", "tickets.ticket.classify", "tickets.ticket.assign", "tickets.ticket.delete", "masters.master.view", "bugs.attachment.view", "bugs.bug.view")),
    ("reassign", "Reassign Tickets", "ticket_creation", "tickets.ticket.reassign",
     ("tickets.ticket.view", "tickets.ticket.reassign", "tickets.ticket.assign", "masters.master.view", "bugs.bug.view")),
    ("all", "All Tickets", "ticket_management", "tickets.ticket.view", WORK_ACTIONS + BUG_ACTIONS + ACCESS_ACTIONS),
    ("bugs", "Bug Requests", "ticket_management", "tickets.ticket.view", WORK_ACTIONS + BUG_ACTIONS),
    ("services", "Service Requests", "ticket_management", "tickets.ticket.view", WORK_ACTIONS),
    ("access", "Access Requests", "ticket_management", "tickets.ticket.view", WORK_ACTIONS + ACCESS_ACTIONS),
    ("critical", "Critical Tickets", "ticket_management", "tickets.ticket.view", WORK_ACTIONS + BUG_ACTIONS + ACCESS_ACTIONS),
    ("overdue", "Overdue Tickets", "ticket_management", "tickets.ticket.view", WORK_ACTIONS + BUG_ACTIONS + ACCESS_ACTIONS),
    ("testing", "Testing / Verification", "ticket_management", "tickets.ticket.view", WORK_ACTIONS + BUG_ACTIONS),
    ("closed", "Closed Tickets", "ticket_management", "tickets.ticket.view", ("tickets.ticket.view", "bugs.bug.view", "bugs.bug.reopen", "bugs.attachment.view")),
)

TICKET_PAGE_PERMISSIONS = [
    (f"tickets.{key}.access", f"Access {name}", group, key, name, "use")
    for key, name, group, _, _ in TICKET_SUBMODULES
]
