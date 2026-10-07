# ZigTrackr backend

Django 6, MariaDB, Channels, Celery and Redis power the API, chat and mail
pipeline. This directory is intended to be its own Git repository; `.env`,
`media/`, `.venv/`, generated static files and database dumps are ignored.
Keep production credentials outside Git.

## Local development

Create `.env` from `.env.example`, install `requirements.txt` in `.venv`,
configure MariaDB, and run migrations. From this directory:

```bash
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver 127.0.0.1:8021
```

Run Redis on `127.0.0.1:6379` and start these in separate terminals:

```bash
.venv/bin/celery -A config.celery.app worker --loglevel=INFO --queues=mail-intake --concurrency=1
.venv/bin/celery -A config.celery.app worker --loglevel=INFO --queues=mail-outbound --concurrency=2
.venv/bin/celery -A config.celery.app beat --loglevel=INFO
```

Beat schedules IMAP polling and sweeps saved outbound mail jobs. The outbound
worker sends the existing incoming-mail acknowledgement, assignment notice and
closure notice. Ticket requests do not wait for SMTP; failed notices remain in
MariaDB for retry. Login usernames and emails must match saved case exactly.

## Dashboard summary

The redesigned frontend retains the dashboard API contract. Workflow counters
in `apps/dashboard/selectors/kpi.py` use the linked bug status when present,
otherwise the ticket status. Verification, hold and reopen counts therefore
include service/access tickets and do not count stale mirrored bug states.
Linked bugs are not counted twice; standalone legacy bugs remain included.

The new work/attention panels use the existing scoped ticket list; recent
activity uses the Daily Updates endpoint. Existing charts remain bug-only and
are labeled accordingly. Dashboard permissions, row visibility and download
protections are unchanged. No schema migration or new background job is needed.

## User Management inputs

User creation and profile updates accept the basic account/contact fields and
roles, but no longer accept `designation`, `department`, `team`, or `site`
(including their FK `_id` aliases). These inputs receive a field-specific 400
response rather than being silently ignored. The service layer enforces the
same restriction. Existing stored values and read-only user/session metadata
are preserved for team-based ticket visibility and reporting; no database
columns or organization masters are deleted and no migration is required.

## Submodule permissions

Permission Management uses one role checkbox per submodule instead of action
rows. Ticket Creation contains Create Ticket, Unassigned Tickets, and Reassign
Tickets. Ticket Management contains All Tickets, Bug Requests, Service Requests,
Access Requests, Critical Tickets, Overdue Tickets, Testing / Verification,
and Closed Tickets. Each ticket screen has an independent `tickets.<screen>.access`
permission; selecting it grants its associated workflow actions and required
read-only lookup/attachment permissions. Ownership and workflow-state checks
still apply; a screen checkbox does not grant global mutation/visibility scope.

Apply the upgrade with `.venv/bin/python manage.py migrate`, then restart the
backend and refresh the browser. Migration `accounts.0003_ticket_submodule_access`
adds page grants matching existing access, without granting new actions or
resetting customized roles. Fresh installations still use `seed_permissions`
and `seed_roles`. **Do not re-run `seed_roles` to upgrade a customized database**:
that command replaces system role grants with defaults.

The matrix API adds `modules[].submodules` metadata. Role permission updates
accept `{"submodule_changes": [{"key": "ticket_creation.unassigned", "enabled": true}]}`
at `PUT /api/v1/roles/{id}/permissions/`; the legacy `permissions` replacement
payload remains supported. Only changed bundles are written, transactionally
and with an audit record. Shared actions required by another selected screen
are retained when one screen is disabled. Existing partial grants appear mixed
and are not automatically promoted to full action access. Admin role and
permission management access cannot be removed through the matrix.

Ticket list pages send `submodule=unassigned|reassign|all|bugs|services|access|critical|overdue|testing|closed`.
The API checks the corresponding page permission and applies its preset on
the server. Additional filters only narrow it. Generic lists for chat and
legacy API clients retain the existing action and row-visibility checks.

## Daily ticket activity

Authenticated `GET /api/v1/tickets/daily-updates/` accepts ISO `from_date` and
`to_date` (inclusive, default today), `category` (`all`, `unassigned`, `assigned`,
`rectified`, `closed`), `search`, `page` and `limit` (maximum 100). It returns
historical activity and written/email updates, category counts, and paginated
rows across bug, service and access tickets. Automatic lifecycle notes are
excluded when the action is already represented by a ticket activity.

`GET /api/v1/tickets/daily-updates/calendar/?month=2026-10-01` returns daily
counts for the calendar grid, including adjacent dates. Both endpoints require
`bugs.update.view` and `tickets.ticket.view`, scope tickets and linked bugs to
the caller, and exclude deleted records before counting. The day boundaries
use Django's configured timezone without requiring MariaDB timezone tables.
Lifecycle categories describe the recorded event, not the ticket's current
status. Ownership on written bug updates is its saved snapshot; other rows
show the current ticket owner. No new migrations or background tasks are needed.

## Production

Use `config.settings.prod`, HTTPS, one trusted reverse proxy, and separate
Redis databases: Celery `/0`, Django cache `/1`, Channels `/2`. Configure
SMTP whenever `TICKET_ACK_ENABLED=true`, even if mail intake is disabled.
Back up MariaDB and `media/`; do not publish `media/` as static files. Run
`manage.py check --deploy` and apply migrations before restarting services.
Systemd units, an Nginx example, outbox monitoring and retry instructions are
in [deploy/systemd/README.md](deploy/systemd/README.md). Follow the
[release checklist](deploy/RELEASE_CHECKLIST.md) before opening the VPS to users.

Do not claim capacity for 1,000 concurrent users from the account count alone.
Measure ticket action latency and queue depth on staging first.
