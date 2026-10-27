#!/usr/bin/env bash
# The whole of PEACE on this Linux desktop, as systemd user services: its own PostgreSQL cluster, the API with
# the dashboard, the sync worker and the desktop agent. No root needed here (../install.sh installs packages).
# Safe to run again: existing secrets, settings, database and data are kept.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
repo=$(dirname "$here")
conf=~/.config/peace
pgdata=~/.local/share/peace/postgres
socket=${XDG_RUNTIME_DIR:?XDG_RUNTIME_DIR is not set}/peace-pg
units=~/.config/systemd/user

# Debian/Ubuntu keep the server binaries out of PATH.
for dir in /usr/lib/postgresql/*/bin; do [ -x "$dir/initdb" ] && PATH="$dir:$PATH"; done
for tool in initdb postgres pg_ctl psql createdb createuser; do
  command -v "$tool" > /dev/null || { echo "missing $tool: install PostgreSQL first" >&2; exit 1; }
done
pgbin=$(dirname "$(command -v postgres)")
[ -x "$repo/.venv/bin/peace-api" ] || { echo "missing $repo/.venv: run 'uv sync --frozen' in $repo" >&2; exit 1; }
[ -f "$repo/dashboard/dist/index.html" ] || { echo "missing dashboard build: run 'npm ci && npm run build' in $repo/dashboard" >&2; exit 1; }

umask 077
mkdir -p "$conf" ~/.local/share/peace/data "$units" ~/.local/share/applications ~/.local/share/icons/hicolor/scalable/apps

# Secrets: one API token for the API and the desktop agent; academic credentials are added by hand (mode 600).
token() { python3 -c 'import secrets; print(secrets.token_urlsafe(32))'; }
if ! grep -qs '^PEACE_API_TOKEN=' "$conf/peace.env"; then
  printf 'PEACE_API_TOKEN=%s\n' "$(token)" >> "$conf/peace.env"
fi
chmod 600 "$conf/peace.env"
api_token=$(grep -m1 '^PEACE_API_TOKEN=' "$conf/peace.env" | cut -d= -f2-)
if [ ! -s "$conf/desktop.env" ]; then
  printf 'PEACE_URL=http://127.0.0.1:8420\nPEACE_TOKEN=%s\n' "$api_token" > "$conf/desktop.env"
fi
for setting in 'PEACE_PTT=SUPER + Y' 'PEACE_TTS=supertonic3' 'PEACE_TTS_VOICE=F1'; do
  grep -q "^${setting%%=*}=" "$conf/desktop.env" || printf '%s\n' "$setting" >> "$conf/desktop.env"
done
chmod 600 "$conf/desktop.env"

# Database cluster: created once. Peer authentication over the private socket; the OS user may connect as
# itself (administration) or as the unprivileged "peace" role the services use. No TCP listener at all.
if [ ! -s "$pgdata/PG_VERSION" ]; then
  initdb -D "$pgdata" -U "$USER" --auth-local=peer --auth-host=reject --encoding=UTF8 --locale=C.UTF-8 > /dev/null
  printf 'local all all peer map=peace\n' > "$pgdata/pg_hba.conf"
  printf 'peace %s %s\npeace %s peace\n' "$USER" "$USER" "$USER" > "$pgdata/pg_ident.conf"
  fresh=1
fi

# The units name the repository and PostgreSQL by path: point them at this checkout and these binaries.
for unit in "$here"/systemd/local/peace-*.service "$here"/systemd/peace-desktop.service "$here"/systemd/peace-voice.service; do
  [ -f "$unit" ] || continue
  sed -e "s|%h/peace/|$repo/|g" -e "s|/usr/bin/postgres |$pgbin/postgres |" "$unit" > "$units/$(basename "$unit")"
  chmod 644 "$units/$(basename "$unit")"
done
install -m644 "$here/peace.desktop" ~/.local/share/applications/peace.desktop
install -m644 "$repo/dashboard/public/icon.svg" ~/.local/share/icons/hicolor/scalable/apps/peace.svg
systemctl --user daemon-reload
systemctl --user enable --now peace-db.service
if [ "${fresh:-0}" = 1 ]; then
  createuser -h "$socket" --no-superuser --no-createdb --no-createrole peace
  createdb -h "$socket" -O peace peace
fi

# Runtime Hyprland bindings and actions need the compositor's variables in the user manager.
session_vars=()
for name in WAYLAND_DISPLAY HYPRLAND_INSTANCE_SIGNATURE XDG_CURRENT_DESKTOP; do
  [ -z "${!name:-}" ] || session_vars+=("$name")
done
((${#session_vars[@]} == 0)) || systemctl --user import-environment "${session_vars[@]}"
systemctl --user enable --now peace-api.service peace-worker.service peace-desktop.service

# Brave (optional): the native-messaging host manifest, pointing at this repository's host script, admitting only
# the PEACE extension's fixed ID. The extension itself is loaded by hand (brave://extensions → Load unpacked).
if [ -d ~/.config/BraveSoftware/Brave-Browser ]; then
  hosts=~/.config/BraveSoftware/Brave-Browser/NativeMessagingHosts
  mkdir -p "$hosts"
  sed "s|@HOST_PATH@|$here/brave/peace_browser_host.py|" "$here/brave/com.peace.browser.json" > "$hosts/com.peace.browser.json"
  chmod 644 "$hosts/com.peace.browser.json"
fi

curl -fsS -o /dev/null --max-time 5 http://127.0.0.1:8420/health && echo "PEACE is running locally: http://127.0.0.1:8420"
[ ! -d ~/.config/BraveSoftware/Brave-Browser ] || echo "Brave (optional): brave://extensions → Developer mode → «Load unpacked» → $here/brave/extension"
