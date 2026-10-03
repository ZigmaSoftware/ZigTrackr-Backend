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
