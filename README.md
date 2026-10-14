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
closure notice, plus reason-based unassigned deletion notices. Ticket requests do not wait for SMTP; failed notices remain in
MariaDB for retry. Login usernames and emails must match saved case exactly.

## Unassigned request actions and files

The row menu has two audited soft-deletion actions, using existing delete
permissions and unassigned-state checks. **Not a Ticket** requires confirmation
in the UI and does not queue a deletion notice. **Delete** requires a nonblank
reason (maximum 2,000 characters) and queues it to the sender in the same database
transaction as deletion. Existing mail records and files remain retained.

`DELETE /api/v1/tickets/{id}/` requires an explicit JSON body:

```json
{"disposition": "NOT_A_TICKET"}
```

or:

```json
{"disposition": "DELETE", "reason": "This request is already tracked under another reference."}
```

The result includes `notification_queued`, not delivery confirmation. Mail uses
the existing `mail-outbound` worker and Beat recovery, stable Message-IDs and
reply threading. Missing, system/no-reply addresses or `TICKET_ACK_ENABLED=false`
prevent notification; the audit still records the deletion reason.

Ticket lists include `email_attachment_count`, computed without per-row queries.
`GET /tickets/{id}/attachments/?source=MAIL` lists original and linked reply files,
excluding rejected files, empty storage paths and internal uploads. Previews and
downloads use the existing authenticated, visibility-checked endpoint; deleted
tickets are not downloadable. No new migration is required for these changes.

## Login sessions

Manual logout clears the UI and cached account data immediately; the protected
server logout runs with a five-second browser timeout. Logout works even when
the access cookie has expired, is idempotent, and revokes both access and
refresh credentials through a server-side session record. All auth responses
are `Cache-Control: no-store`; refresh and logout require CSRF protection.

The default policy is **30 minutes idle**, a **one-minute warning**, and an
**eight-hour absolute limit**. Set `AUTH_SESSION_IDLE_MINUTES=30`,
`AUTH_SESSION_MAX_HOURS=8`, and `JWT_ACCESS_MINUTES=5` in `.env`. Tokens remain
HttpOnly; even if `JWT_REFRESH_DAYS` is seven, issued refresh tokens/cookies are
capped at the original session deadline. Rotation never resets that deadline.

`POST /api/v1/auth/activity/` records user interaction. The frontend sends it
at most once a minute while interacting (or explicitly on Stay signed in).
Polling, `/auth/me/`, refresh, and chat traffic do not extend idle time.
Expiry is checked by the backend on every authenticated request, refresh and
staff chat authorization; password changes also invalidate old access tokens.

State is stored in Django's existing `django_session` table, not a per-process
cache, so revocation works across API processes and restarts. No new migration
or extra service is required if normal migrations have already been applied.
Deploy backend and frontend together, restart the backend, and sign in again:
old tokens without session claims are intentionally rejected. Run
`.venv/bin/python manage.py clearsessions` and
`.venv/bin/python manage.py flushexpiredtokens` daily to prune expired records.

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
