#!/usr/bin/env bash
# Docker: run once before `docker compose up`. Creates missing secrets; never overwrites existing ones.
# Add MOODLE_URL / INFORESTUDANTE_URL to secrets/peace.env (see README); logins go in the dashboard.
set -euo pipefail
dir=${1:-$(dirname "$0")/../secrets}
umask 077
mkdir -p "$dir"
token() { python3 -c 'import secrets; print(secrets.token_urlsafe(32))'; }
[ -s "$dir/db_password" ] || token > "$dir/db_password"
grep -q '^PEACE_API_TOKEN=' "$dir/peace.env" 2>/dev/null || echo "PEACE_API_TOKEN=$(token)" >> "$dir/peace.env"
chmod 600 "$dir"/*
echo "secrets ready in $dir"
