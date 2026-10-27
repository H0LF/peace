#!/usr/bin/env bash
# Restore a PEACE database backup into a NEW database; never into an existing one.
#   restore.sh <backup.dump> [target-db]     (target default: peace_restore_<UTC yyyymmdd_hhmmss>)
# Checks the checksum and reads the dump in full first. The restore is one transaction; on failure the new
# database is dropped again. Switching the app to the restored database is a manual step (docs/operations/BACKUP.md).
# Uses the same PEACE_BACKUP_MODE / PEACE_COMPOSE / libpq settings as backup.sh.
set -euo pipefail
umask 077
file=${1:?usage: restore.sh <backup.dump> [target-db]}
target=${2:-peace_restore_$(date -u +%Y%m%d_%H%M%S)}
mode=${PEACE_BACKUP_MODE:-compose}
read -r -a compose <<< "${PEACE_COMPOSE:-docker compose}"
file=$(realpath "$file")
cd "$(dirname "$0")/.."

log() { printf '%s peace-restore %s\n' "$(date -u +%FT%TZ)" "$*" >&2; }
fail() { log "FAILED: $*"; exit 1; }
pg() { if [[ $mode == compose ]]; then "${compose[@]}" exec -T db "$1" -U peace "${@:2}"; else "$@"; fi; }
[[ $target =~ ^[a-z_][a-z0-9_]*$ ]] || fail "target must be a plain lower-case database name"

[[ -f $file.sha256 ]] || fail "no checksum file next to the backup"
(cd "$(dirname "$file")" && sha256sum --quiet -c "$(basename "$file").sha256") || fail "checksum mismatch"
pg pg_restore -f /dev/null < "$file" || fail "backup cannot be read in full"

log "creating new database $target"
pg createdb "$target" || fail "could not create $target (it may already exist; nothing was changed)"
if ! pg pg_restore --exit-on-error --single-transaction --no-owner -d "$target" < "$file"; then
  pg dropdb "$target" || log "could not drop the incomplete $target; drop it by hand"
  fail "restore failed; $target removed"
fi
version=$(pg psql -d "$target" -Atc 'SELECT version_num FROM alembic_version')
tasks=$(pg psql -d "$target" -Atc 'SELECT count(*) FROM tasks')
log "ok: $target restored (migration $version, $tasks tasks)"
