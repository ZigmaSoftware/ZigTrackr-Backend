"""Row-level bug visibility (spec 14).

Every endpoint that returns Bug rows -- list screens, dashboard cards, reports,
search, exports -- must route its queryset through scope_bug_queryset(). An
endpoint that forgets is a data leak, so BugViewSet applies it in
get_queryset() and report selectors take an already-scoped queryset as input
rather than starting from Bug.objects.

Tiers, widest first:
  view_all   -> everything                                  (Admin, Management)
  view_team  -> the caller's team, plus their own            (Team Lead)
  default    -> bugs the caller owns, reported, or assigned  (Developer, QA, Reporter)

Testers additionally see TESTING and REOPENED bugs so they can verify fixes
and review requester reopenings, even when they do not own the bug.

VISIBILITY IS NOT WRITE AUTHORITY. scope_bug_queryset() answers "can this user
see this row"; can_mutate_bug() below answers "can this user change this row",
and the two intentionally disagree for Management (view_all, but no mutate
rights at all -- spec 14's read-only role) and partially for Testers (can
mutate only while a bug is actually in TESTING, not everything view_all-style
visibility would suggest).
"""

from django.db.models import Q

from common.permissions.require import has_permission


def scope_bug_queryset(queryset, user):
    if user is None or not getattr(user, "is_authenticated", False):
        return queryset.none()

    if getattr(user, "is_superuser", False) or has_permission(user, "bugs.bug.view_all"):
        return queryset

    base = Q(owner=user) | Q(reported_by=user) | Q(assigned_by=user)

    if has_permission(user, "bugs.bug.view_team"):
        if getattr(user, "team_id", None):
            base |= Q(owner__team_id=user.team_id) | Q(reported_by__team_id=user.team_id)
        # Unassigned bugs are the Team Lead's inbox. Spec 16 puts "Unassigned
        # Bugs" in the sidebar and spec 37 gives leads an assignment screen, so
        # a bug nobody owns must be visible to whoever is expected to triage it
        # -- otherwise a bug reported by someone outside the team is invisible
        # to every lead and can never be assigned.
        base |= Q(owner__isnull=True)

    if has_permission(user, "bugs.bug.assign"):
        base |= Q(owner__isnull=True)

    # Developers can discover newly received [BUG] mail in All Bugs while it
    # waits for routing. Visibility alone grants no status or edit authority:
    # can_mutate_bug still requires ownership, authorship or lead authority.
    if has_permission(user, "bugs.bug.view_unassigned_email"):
        base |= Q(
            owner__isnull=True,
            support_ticket__source="EMAIL",
            support_ticket__mails__is_thread_reply=False,
            support_ticket__needs_review=False,
        )

    if has_permission(user, "bugs.bug.test"):
        from apps.bugs.constants import BugStatus
        base |= Q(status__in=(BugStatus.TESTING, BugStatus.REOPENED))

    # distinct(): the team branch joins through owner__team and
    # reported_by__team, which can otherwise duplicate rows and corrupt
    # pagination counts.
    return queryset.filter(base).distinct()


def can_view_bug(user, bug):
    """Single-object gate for detail and attachment-download paths."""
    from apps.bugs.models import Bug

    return scope_bug_queryset(Bug.objects.filter(pk=bug.pk), user).exists()


def can_mutate_bug(user, bug):
    """Write gate beyond the codename check.

    A Developer may act on their own bugs; Team Leads act across their team;
    Admins are unrestricted. The codename check decides *whether* an action
    exists for this user, this decides *which rows* they may apply it to.

    Deliberately keyed on `bugs.bug.mutate_all`, NOT `bugs.bug.view_all`.
    Management holds view_all (so it can see everything for reporting) but
    must NOT be able to write to bugs it does not own -- it is a read-only
    role by design (spec 14). Treating view_all as mutate authority let a
    read-only role write through any endpoint whose codename gate admitted
    it (e.g. bugs.update.view on the updates action). Visibility and write
    authority are separate questions with separate answers.
    """
    if getattr(user, "is_superuser", False) or has_permission(user, "bugs.bug.mutate_all"):
        return True

    if has_permission(user, "bugs.bug.view_team"):
        team_id = getattr(user, "team_id", None)
        if team_id and bug.owner_id and bug.owner.team_id == team_id:
            return True
        # Triaging an unassigned bug is the point of the assignment screen
        # (spec 37); seeing it without being able to assign it would be useless.
        if bug.owner_id is None:
            return True

    if has_permission(user, "bugs.bug.assign") and bug.owner_id is None:
        return True

    if bug.owner_id == user.id or bug.reported_by_id == user.id:
        return True

    # A tester's visibility scope includes TESTING and REOPENED bugs company-wide,
    # but mutation through the bug API is granted only for TESTING, and
    # recording a test result is the one action that queue exists for. Without
    # this, testing() had to skip can_mutate_bug entirely to work at all --
    # which then let a tester force-transition any bug, not just ones in
    # TESTING. Scoping the grant to the current status keeps it to exactly the
    # queue the tester is meant to review.
    if has_permission(user, "bugs.bug.test"):
        from apps.bugs.constants import BugStatus
        if bug.status == BugStatus.TESTING:
            return True

    return False


# ---- SUPPORT TICKETS ----
# Deliberately mirrors the bug functions above rather than generalising them:
# the tiers differ (tickets have no team/testing concept) and a single clever
# helper parameterised over both would be harder to audit than two explicit
# ones. Row-level access is the last thing that should be hard to read.

def scope_ticket_queryset(queryset, user):
    """Row-level SupportTicket visibility.

    Tiers, widest first:
      view_all -> everything                          (Admin, Management)
      classify -> everything not yet confirmed        (Team Lead triage queue)
      default  -> tickets the caller owns or reported
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return queryset.none()

    if getattr(user, "is_superuser", False) or has_permission(user, "tickets.ticket.view_all"):
        return queryset

    base = Q(owner=user) | Q(reported_by=user)

    # Whoever triages the queue has to see what is in it, including tickets
    # raised by people outside their team -- otherwise an email nobody owns is
    # invisible to every lead and can never be confirmed.
    if has_permission(user, "tickets.ticket.classify"):
        base |= Q(needs_review=True) | Q(owner__isnull=True)
        if getattr(user, "team_id", None):
            base |= Q(owner__team_id=user.team_id)

    if has_permission(user, "tickets.ticket.assign"):
        base |= Q(owner__isnull=True)

    if has_permission(user, "tickets.ticket.verify_close"):
        base |= Q(bug__status__in=("TESTING", "REOPENED")) | Q(
            bug__isnull=True, status__in=("TESTING", "REOPENED")
        )

    return queryset.filter(base).distinct()


def can_view_ticket(user, ticket):
    """Single-object gate for ticket detail.

    A BUG ticket carries the linked bug's title and description, so it must not
    become a side channel around bug scoping: if the caller cannot see the bug,
    they cannot see the ticket that mirrors it.
    """
    from apps.tickets.models import SupportTicket

    visible = scope_ticket_queryset(
        SupportTicket.objects.filter(pk=ticket.pk), user
    ).exists()
    if not visible:
        return False

    if getattr(ticket, "bug_id", None):
        return can_view_bug(user, ticket.bug)

    return True


def can_mutate_ticket(user, ticket):
    """Write gate beyond the codename check.

    Keyed on `tickets.ticket.mutate_all`, NOT `tickets.ticket.view_all`, for
    exactly the reason documented on can_mutate_bug: Management holds view_all
    for reporting and must never be able to write through an endpoint whose
    codename gate happens to admit it.
    """
    if getattr(user, "is_superuser", False) or has_permission(user, "tickets.ticket.mutate_all"):
        return True

    # Triage authority covers the review queue and anything unowned -- that is
    # the queue the classify permission exists to work through.
    if has_permission(user, "tickets.ticket.classify"):
        if ticket.needs_review or ticket.owner_id is None:
            return True
        if getattr(user, "team_id", None) and ticket.owner_id and ticket.owner.team_id == user.team_id:
            return True

    if has_permission(user, "tickets.ticket.assign") and ticket.owner_id is None:
        return True

    if has_permission(user, "tickets.ticket.verify_close") and (
        (ticket.bug.status in ("TESTING", "REOPENED") if ticket.bug_id
         else ticket.status in ("TESTING", "REOPENED"))
    ):
        return True

    if ticket.owner_id == user.id or ticket.reported_by_id == user.id:
        return True

    return False


# ---- MAIL INTAKE ----

def scope_mail_queryset(queryset, user):
    """Row-level MailIntake visibility.

    Intake mail is operational data, not personal correspondence, but it still
    contains whatever an external sender chose to write. Only holders of
    view_all see the whole mailbox; everyone else sees mail they sent in or mail
    attached to a ticket they can already see.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return queryset.none()

    if getattr(user, "is_superuser", False) or has_permission(user, "mail_intake.mail.view_all"):
        return queryset

    base = Q(pk__in=[])

    # Triage roles need the unlinked and failed mail -- that is the work.
    if has_permission(user, "mail_intake.mail.confirm_classification"):
        base |= Q(linked_ticket__isnull=True) | Q(linked_ticket__needs_review=True)

    if has_permission(user, "mail_intake.mail.reprocess"):
        base |= Q(linked_ticket__isnull=True)

    email = (getattr(user, "email", "") or "").strip().lower()
    if email:
        base |= Q(from_email__iexact=email)

    from apps.tickets.models import SupportTicket

    visible_tickets = scope_ticket_queryset(SupportTicket.objects.all(), user)
    base |= Q(linked_ticket__in=visible_tickets)

    return queryset.filter(base).distinct()
