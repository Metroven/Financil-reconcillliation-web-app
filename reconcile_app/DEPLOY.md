# Deploying Tally

The app ships as a Docker image. Pick one path.

## Option A: your own server (VPS) with automatic HTTPS - recommended
You need: a small Linux server (1 GB RAM is enough), a domain name, and Docker installed.

1. Point your domain's A record at the server's IP address.
2. Copy this folder to the server (`scp -r reconcile_app user@SERVER:~/`), then `cd reconcile_app`.
3. `cp .env.example .env`, then edit `.env`: set `SECRET_KEY` (command inside the file) and `DOMAIN`.
4. Open ports 80 and 443 in the server firewall.
5. `docker compose up -d --build`
6. Visit https://YOUR-DOMAIN. Caddy fetches the HTTPS certificate automatically.

Update later: copy the new files, run `docker compose up -d --build`. Your data is kept.
Logs: `docker compose logs -f app`.

## Option B: a platform that runs Docker (Fly.io, Railway, Render and similar)
- Deploy from this folder; it uses the Dockerfile. The app listens on the port in `$PORT` (default 8000).
- Attach a **persistent volume mounted at `/data`**. Without it, accounts and files vanish on every redeploy.
- Set `SECRET_KEY` as a secret. HTTPS is provided by the platform.
- Run a single instance: the database is SQLite, so do not scale to several machines.

## Option C: Railway (step by step)
1. Put the folder in a private GitHub repository (`.gitignore` already keeps `data/` and `.env` out).
2. Railway: New Project > Deploy from GitHub repo > pick the repo. It finds the `Dockerfile` by itself.
3. In the service's Variables add:
   - `SECRET_KEY` = a long random string (`python3 -c "import secrets; print(secrets.token_hex(32))"`)
   - `RAILWAY_RUN_UID` = `0` (lets the app write to the volume)
   - optional: `ALLOW_REGISTRATION` = `0` to close sign-ups later
4. Add a Volume to the service with mount path `/data`. Do this before real use.
5. In the service Settings: generate a public domain, and set the healthcheck path to `/healthz`.
6. Custom domain: add it under Networking and create the DNS record Railway shows at your registrar.
7. Test: register, upload a file, trigger a redeploy, and confirm your account and file are still there.
Notes: one instance only (Railway does not allow replicas with volumes); each redeploy causes a brief outage.

## Backups (important: this holds users' financial files)
Everything is in the `/data` volume (database, uploads, session key).
    docker compose exec app python -c "import sqlite3;s=sqlite3.connect('/data/recon.db');d=sqlite3.connect('/data/backup.db');s.backup(d)"
    docker compose cp app:/data ./backup-$(date +%F)
Run this on a schedule (cron) and copy the result off the server.

## Settings (environment variables)
| Name | Purpose |
|---|---|
| SECRET_KEY | Signs login sessions. Required in production; keep it stable and secret. |
| ALLOW_REGISTRATION | 1 = anyone can sign up (default). 0 = closed. |
| COOKIE_SECURE | 1 when served over HTTPS (set by the Docker setup). |
| TRUST_PROXY | 1 when behind Caddy or a platform proxy (set by the Docker setup). |
| RECON_DATA_DIR | Where data is stored (`/data` in Docker). |

## Built in
Passwords hashed; CSRF tokens on every form; users only see their own data; login lockout after
5 failed attempts per 15 minutes; 10 MB upload limit and 50,000 rows per file; `/healthz` for monitors.

## Before you open it to the public
- Decide who may sign up. With sign-ups open, anyone can store files on your server. Use
  `ALLOW_REGISTRATION=0` after your users have registered.
- You are holding financial data. Tell users how long files are kept, and consider adding a
  privacy notice. Files are stored unencrypted on the server disk, so use disk encryption from your host.
- There is no password reset or email yet. A forgotten password needs an administrator.
