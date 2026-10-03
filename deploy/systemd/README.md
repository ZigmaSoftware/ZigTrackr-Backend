# Application and Celery services

These templates assume the backend checkout is
`/home/administrator/Bug-Tracker/Backend`, the frontend checkout is
`/home/administrator/Bug-Tracker/Frontend`, and the service account is
`administrator`. With separate Git repositories, clone them to those paths or
edit every `User`, `WorkingDirectory`, `ExecStart`, and Nginx `root` before
installing the units. Nothing here initializes or pushes a Git repository.

Install Redis and create `/var/lib/zigma` owned by `administrator`, then copy
the four unit files into `/etc/systemd/system/` and run:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now redis-server zigma-asgi zigma-celery-worker zigma-celery-outbound zigma-celery-beat
sudo systemctl status zigma-asgi zigma-celery-worker zigma-celery-outbound zigma-celery-beat
```

The Django application must load `config.settings.prod` and provide the same
database/mail environment as the other units. Route HTTP and WebSocket
requests for `/api/` to Daphne on `127.0.0.1:8021`; preserve the
`Upgrade` and `Connection` headers in the reverse proxy. Set
`CHANNEL_REDIS_URL` to Redis database 2 and `CACHE_REDIS_URL` to database 1;
apply migrations
before restarting. WSGI/Gunicorn cannot serve ticket chat sockets.

The intake worker listens to `mail-intake`; the outbound worker listens to
`mail-outbound`. Beat also recovers pending requester mail every minute. The
Nginx template at `deploy/nginx/zigtrackr.conf.example` must have its domain
and certificate paths replaced and pass `nginx -t` before enabling it. The
proxy overwrites forwarded headers rather than trusting the browser's values.

Requester mail jobs persist in `ticket_outbound_mail`, even if Redis or SMTP
is unavailable. Check status counts without exposing recipient addresses:

```sh
cd /home/administrator/Bug-Tracker/Backend
DJANGO_SETTINGS_MODULE=config.settings.prod .venv/bin/python manage.py shell -c 'from apps.tickets.models import OutboundMail; from django.db.models import Count; print(list(OutboundMail.objects.values("status").annotate(total=Count("id"))))'
```

After fixing SMTP, retry a FAILED job with `manage.py retry_outbound_mail
JOB_ID`. SMTP cannot guarantee exactly-once delivery: a crash after SMTP
accepts a message but before success is recorded may produce a duplicate.

Keep `manage.py process_support_mail` available for manual recovery; do not
run cron and Celery Beat at the same time.
