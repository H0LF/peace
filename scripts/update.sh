#!/usr/bin/env bash
# Installs a new version of PEACE when PEACE asks for it (Settings > Geral). Runs on the computer, not in Docker, every few
# minutes (launchd on macOS, a systemd timer on Linux; both set up by the installers). Does nothing unless an update is due.
# Order: ask, download, verify the signature, back up the database, swap the code, start, check health. If PEACE does
# not come back: the old code and the database backup go back in place. Your data and settings are never in the code folder.
# Environment for tests: PEACE_HOME, PEACE_URL, HEALTH_TRIES, HEALTH_WAIT.
set -uo pipefail

self=$(cd "$(dirname "$0")/.." && pwd)
kind=${PEACE_KIND:-$( [ -f "$self/compose.yaml" ] && [ -f "$self/.env" ] && echo docker || echo linux )}
app=$self
top=$(dirname "$app")
url=${PEACE_URL:-http://127.0.0.1:8420}
tries=${HEALTH_TRIES:-120}
wait_s=${HEALTH_WAIT:-5}
state=${PEACE_HOME:-$top/.peace-update}
mkdir -p "$state"

if [ "$kind" = docker ]; then
  env_file=$(sed -n 's/^PEACE_ENV_FILE=//p' "$app/.env")
else
  env_file=${PEACE_ENV_FILE:-$HOME/.config/peace/peace.env}
fi
token=$(sed -n 's/^PEACE_API_TOKEN=//p' "$env_file" | head -1)
[ -n "$token" ] || { echo "no token in $env_file" >&2; exit 1; }

api() { curl -fsS --max-time 20 -H "Authorization: Bearer $token" -H 'Content-Type: application/json' "$@"; }
report() { api -X POST "$url/api/v1/updates/report" -d "{\"status\":\"$1\",\"message\":\"$2\"}" > /dev/null || true; }
field() {  # shortcut: sed over one flat JSON object; GitHub URLs and x.y.z versions hold no quotes
  sed -n "s/.*\"$1\":\"\([^\"]*\)\".*/\1/p"
}

pending=$(api "$url/api/v1/updates/pending") || exit 0  # PEACE is off: try again next time
case "$pending" in *'"install":true'*) ;; *) exit 0 ;; esac
version=$(printf '%s' "$pending" | field version)
bundle=$(printf '%s' "$pending" | field bundle)
signature=$(printf '%s' "$pending" | field signature)
exec 9> "$state/lock"
if command -v flock > /dev/null; then flock -n 9 || exit 0; else mkdir "$state/lock.d" 2> /dev/null || exit 0; trap 'rmdir "$state/lock.d"' EXIT; fi

work=$(mktemp -d "$top/.peace-update-XXXXXX")
fail() {
  report failed "$1"
  echo "update to $version failed: $1" >&2
  rm -rf "$work"
  exit 1
}
report installing "$version"

curl -fsSL --max-time 900 -o "$work/peace.tar.gz" "$bundle" || fail download
curl -fsSL --max-time 60 -o "$work/peace.sig" "$signature" || fail download
mkdir "$work/new"
compose() { (cd "$app" && docker compose "$@"); }
if [ "$kind" = docker ]; then
  # Verified by the running version's own code and key, inside its container: the host needs no Python.
  compose run --rm --no-deps -T --user "$(id -u)" -v "$work:/update" api python -m peace.release unpack /update/peace.tar.gz /update/peace.sig "$version" /update/new || fail signature
else
  "$app/.venv/bin/python" -m peace.release unpack "$work/peace.tar.gz" "$work/peace.sig" "$version" "$work/new" || fail signature
fi

backup="$state/before-$version.dump"
if [ "$kind" = docker ]; then
  compose exec -T db pg_dump -U peace -Fc peace > "$backup" || fail backup
else
  db=$(sed -n 's/^PEACE_DATABASE_URL=//p' "$env_file" | head -1 | sed 's/+psycopg//')
  pg_dump -Fc "$db" > "$backup" || fail backup
fi
[ -s "$backup" ] || fail backup

start() {
  if [ "$kind" = docker ]; then
    compose up -d --build
  else
    (cd "$app" && { command -v uv > /dev/null && uv sync --frozen --no-dev -q || "$HOME/.local/bin/uv" sync --frozen --no-dev -q; }) && systemctl --user restart peace-api peace-worker && { systemctl --user try-restart peace-desktop || true; }
  fi
}
healthy() {
  for _ in $(seq "$tries"); do
    curl -fsS --max-time 3 "$url/health" > /dev/null 2>&1 && return 0
    sleep "$wait_s"
  done
  return 1
}

[ ! -e "$app/.env" ] || cp -p "$app/.env" "$work/new/.env"  # says where the secrets are: all that lives beside the code
rm -rf "$app.previous"
mv "$app" "$app.previous" || fail swap
mv "$work/new" "$app" || { mv "$app.previous" "$app"; fail swap; }

if start && healthy; then
  rm -rf "$work"
  report done "$version"
  echo "PEACE updated to $version"
  exit 0
fi

# Back to what worked: the code, and the database as it was before the new version could migrate it.
echo "version $version did not start: going back" >&2
rm -rf "$app.failed"
mv "$app" "$app.failed"
mv "$app.previous" "$app"
if [ "$kind" = docker ]; then
  compose up -d db
  compose exec -T db pg_restore -U peace -d peace --clean --if-exists < "$backup"
else
  pg_restore -d "$db" --clean --if-exists "$backup"
fi
start
if healthy; then
  fail start
fi
rm -rf "$work"
echo "PEACE did not come back after going back. Backup of the database: $backup" >&2
exit 2
