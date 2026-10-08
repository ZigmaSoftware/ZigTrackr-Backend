# ZigTrackr on the 192.168.1.128 LAN host

These user services run as `admin` and start at boot with lingering enabled. The
frontend is served at `http://192.168.1.128:1811/` by Nginx; Daphne serves the
API and WebSockets at `http://192.168.1.128:3299/`. The frontend proxies `/api/`
to Daphne so browser cookies and sockets use one origin. `192.168.1.48` can open
these addresses as another machine on the LAN; it is not assigned to this host.

The services load `config.settings.lan`, which keeps production behavior but
allows private HTTP access. Use HTTPS with a trusted certificate and
`config.settings.prod` before exposing the app beyond the trusted LAN.

Run on this host:

```sh
cd /home/admin/Bug-Tracker/ZigTrackr-Backend
python3 -m venv .venv
/home/admin/.local/bin/uv pip install --python .venv/bin/python -r requirements.txt
DJANGO_SETTINGS_MODULE=config.settings.lan .venv/bin/python manage.py migrate
DJANGO_SETTINGS_MODULE=config.settings.lan .venv/bin/python manage.py collectstatic --noinput
DJANGO_SETTINGS_MODULE=config.settings.lan .venv/bin/python manage.py seed_permissions
DJANGO_SETTINGS_MODULE=config.settings.lan .venv/bin/python manage.py seed_roles
DJANGO_SETTINGS_MODULE=config.settings.lan .venv/bin/python manage.py seed_masters --skip-org
DJANGO_SETTINGS_MODULE=config.settings.lan .venv/bin/python manage.py seed_classification_rules

cd /home/admin/Bug-Tracker/ZigTrackr-Frontend
npm ci
npm run build

loginctl enable-linger admin
systemctl --user enable --now \
  /home/admin/Bug-Tracker/ZigTrackr-Backend/deploy/systemd/user/zigtrackr-backend.service \
  /home/admin/Bug-Tracker/ZigTrackr-Backend/deploy/systemd/user/zigtrackr-celery-intake.service \
  /home/admin/Bug-Tracker/ZigTrackr-Backend/deploy/systemd/user/zigtrackr-celery-outbound.service \
  /home/admin/Bug-Tracker/ZigTrackr-Backend/deploy/systemd/user/zigtrackr-celery-beat.service \
  /home/admin/Bug-Tracker/ZigTrackr-Backend/deploy/systemd/user/zigtrackr-frontend.service
```

The existing host Redis service stays bound to loopback. ZigTrackr uses Redis
database 3 for Celery, 4 for Django cache, and 5 for Channels. One Beat instance
schedules intake and outbound recovery; do not add a separate mail poller.

## Allow service ports through UFW

Run this once on `192.168.1.128` in a terminal with administrator access:

```sh
sudo sh /home/admin/Bug-Tracker/ZigTrackr-Backend/deploy/systemd/user/allow-lan-access.sh
```

The script creates port-only allowances for `1811/tcp` and `3299/tcp`, shown
as `ALLOW IN Anywhere`, matching the host's other application services. With
UFW IPv6 support enabled, corresponding IPv6 allowances are also created.
Re-running the script clears comments from the existing port-only rules.
These rules allow any source that can reach the server, across its network
interfaces. The script then removes the earlier ZigTrackr rules restricted to
`eno1`, `192.168.1.128`, and `192.168.0.0/22`.

The browser uses `1811`; Nginx forwards its API and WebSocket requests to
`3299`. Other services' firewall rules stay in place, and no service restart
is needed. Open `http://192.168.1.128:1811/` as before.

Verify from another LAN computer by opening
`http://192.168.1.128:1811/login`. A successful request from the server itself
does not verify firewall access from other computers. If the browser keeps
loading, inspect `journalctl -k --since '5 minutes ago'` for `[UFW BLOCK]`
entries with `DST=192.168.1.128` and `DPT=1811`.

## Service checks

```sh
systemctl --user status 'zigtrackr-*'
journalctl --user -u zigtrackr-backend -u zigtrackr-frontend -f
journalctl --user -u zigtrackr-celery-intake -u zigtrackr-celery-outbound -u zigtrackr-celery-beat -f
```

MariaDB and Redis are existing system services. Keep them enabled. The backend
`.env` contains database and mail credentials and must remain readable only by
the service account. The first administrator's generated login is saved at
`/home/admin/.local/state/zigtrackr/initial-admin.txt` with mode `600`; change
that password after signing in. The seed commands above are for a fresh database:
`seed_roles` replaces customized role grants on an existing installation.
Backup both the database and `media/` before a release.
