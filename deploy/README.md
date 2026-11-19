# Deploying the family apps (Portainer)

One stack with **Wombat**, **Kooka**, **Wallaby** and **Pelican**. Pocket ID is not in the stack: use yours.

## 1. Pocket ID
Create four OIDC clients (Wombat, Kooka, Wallaby, Pelican). Callback URL of each:
`https://<app address>/api/oidc/pocketid/callback`. Make sure the users' emails count as verified (the apps match a
person by verified email the first time).

## 2. Images
On the Docker host, with the four source folders side by side (`wombat/`, `kooka/`, `wallaby/`, `pelican/`):
```sh
./wombat/deploy/build-images.sh        # SRC=… and KOOKA_DIR=… if your folders are elsewhere / named differently
```
The stack uses these local images (`wombat:latest`, …). After an update: build again, then **Update the stack**.

## 3. Variables
Copy `stack.env.example`, fill it in (addresses, Pocket ID clients, three keys, SMTP, optionally the Pelican
mailbox) and in Portainer → Stacks → Add stack → Web editor paste `docker-compose.yml`, then
"Environment variables" → **Load variables from .env file**. Create `DATA_ROOT` on the host first
(e.g. `mkdir -p /srv/family`).

## 4. Reverse proxy
Forward each public HTTPS address to the published port of the host (Wombat 3004, Kooka 3001, Wallaby 3000,
Pelican 3002 by default). The apps trust the proxy's `X-Forwarded-*` headers. If your proxy runs in Docker you can
instead attach it to the `family` network and point it at `wombat:3004`, `kooka:3001`, …

## 5. First run
1. Before the first start with Wombat, if you already use Kooka/Wallaby/Pelican: back up their data folders and
   copy them into `DATA_ROOT/kooka`, `DATA_ROOT/wallaby`, `DATA_ROOT/pelican`.
2. Open Wombat and sign in: the first person creates the family.
3. Wombat → **Import** → check and confirm.
4. Wombat → **Family**: add the partner, the people, the links (Vaultwarden, Home Assistant…).

## Backups
Everything is under `DATA_ROOT`. Pelican's NAS script (`pelican/scripts/backup-to-nas.sh`) works with the stack:
set `PELICAN_DATA_DIR=$DATA_ROOT/pelican` in its `backup.env` (the container is called `pelican`). The same Borg
repository can also take the other three folders: add `$DATA_ROOT/wombat $DATA_ROOT/kooka $DATA_ROOT/wallaby`
(their `backups/` subfolders hold consistent daily copies of each database).
