# First VPS release checklist

This backend and the frontend are separate checkouts. Do not deploy from a
developer `.env`, and do not expose Django's `media/` directory through Nginx.
The unit files and Nginx example assume both checkouts are in
`/home/administrator/Bug-Tracker/`; edit paths if your VPS differs.

1. Create production credentials outside Git; configure MariaDB, SMTP/IMAP,
   `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `PUBLIC_APP_URL`, HTTPS frontend
   origin, CORS/CSRF origins, Celery `/0`, cache `/1`, and Channels `/2`.
2. Back up MariaDB and `Backend/media/`. Restore both into a staging instance
   and verify attachment downloads before any production migration.
3. Run `manage.py check --deploy --settings=config.settings.prod`,
   `manage.py migrate`, and `manage.py collectstatic`. Build the frontend with
   `npm ci && npm run build`. Do not point Nginx at the development server.
4. Validate the configured Nginx file with `nginx -t`; confirm the login
   location rate-limits, forwarded headers are overwritten, and WebSocket
   upgrades work. Enable Redis, Daphne, intake worker, outbound worker, and
   Beat; do not run cron polling alongside Beat.
5. Smoke-test exact-case login, ticket Assign, manual Create, email intake,
   requester acknowledgements, assignment notices, closure notices, reply
   threading, authorized attachment downloads, and a blocked unauthorized
   download. A ticket action succeeding means its mail job was saved, not that
   SMTP has already delivered it.
6. In staging, simulate Redis and SMTP failures and a worker restart. Confirm
   unsent mail remains in `ticket_outbound_mail`, is recovered, and failed jobs
   are visible and retryable. A crash after SMTP accepts a message can produce
   a duplicate with the same Message-ID.
7. Load-test staging with 25 active users for 15 minutes and a 50-user burst,
   including ticket list, Assign, Review, Close and chat. Record p50/p95,
   error rate, DB CPU, Redis usage, worker queue depth and action SQL counts.
   Target p95 below 2 seconds for write APIs at 25 active users and under 1%
   server errors. Tune the VPS and rerun if the target is missed; 1,000
   accounts do not imply 1,000 concurrent users.
8. After launch, watch `journalctl` for the four services, `ticket_action`
   duration/SQL logs, failed outbox count, queue backlog, disk space, and
   database backup success. Keep a rollback database backup and previous
   application build until the release is stable.

The sample configuration in `deploy/nginx/zigtrackr.conf.example` requires
a real domain and valid TLS certificate. These checks cannot be completed on
this development workspace without the actual staging/VPS host.
