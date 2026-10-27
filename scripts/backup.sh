#!/usr/bin/env bash
# Back up the PEACE database (`backup.sh db`) or the Obsidian vault (`backup.sh vault`).
# A backup only gets its final name after it was read back in full and checksummed; a failed run leaves
# nothing behind and never touches older backups. Retention keeps the newest PEACE_BACKUP_KEEP of each kind.
#
# PEACE_BACKUP_DIR   where backups go (default ~/peace-backups, mode 700, files 600)
# PEACE_BACKUP_KEEP  backups kept per kind (default 14)
# PEACE_BACKUP_MODE  db only: "compose" (default; pg tools inside the db container, no password needed)
#                    or "direct" (local pg tools with libpq settings: PGHOST, PGPORT, PGUSER, PGDATABASE,
#                    password in ~/.pgpass; never put a password on the command line)
# PEACE_COMPOSE      compose command (default "docker compose"), run from the repository root
# PEACE_VAULT        vault only: the vault directory (default ~/Peace Vault); it is only read
# PEACE_BACKUP_COPY_TO  a second place for every new backup, off this disk: a directory (external drive) or
#                    host:directory over SSH (key auth). Verified there by checksum; copies there are never
#                    replaced or deleted. Unset: the backup exists on this disk only (logged as a warning).
set -euo pipefail
umask 077
kind=${1:-}
dir=${PEACE_BACKUP_DIR:-$HOME/peace-backups}
keep=${PEACE_BACKUP_KEEP:-14}
mode=${PEACE_BACKUP_MODE:-compose}
read -r -a compose <<< "${PEACE_COMPOSE:-docker compose}"
vault=${PEACE_VAULT:-$HOME/Peace Vault}
cd "$(dirname "$0")/.."

log() { printf '%s peace-backup[%s] %s\n' "$(date -u +%FT%TZ)" "$kind" "$*" >&2; }
fail() { log "FAILED: $*"; exit 1; }
[[ $kind == db || $kind == vault ]] || { echo "usage: $0 db|vault" >&2; exit 2; }
[[ $keep =~ ^[1-9][0-9]*$ ]] || fail "PEACE_BACKUP_KEEP must be a positive integer"
[[ $mode == compose || $mode == direct ]] || fail "PEACE_BACKUP_MODE must be compose or direct"

# Run a PostgreSQL client tool inside the db container or locally.
pg() { if [[ $mode == compose ]]; then "${compose[@]}" exec -T db "$1" -U peace "${@:2}"; else "$@"; fi; }

mkdir -p "$dir"
chmod 700 "$dir"
exec 9> "$dir/.lock"
flock -n 9 || fail "another backup is running"

if [[ $kind == db ]]; then prefix=peace ext=dump; else prefix=vault ext=tar.gz; fi
name="$prefix-$(date -u +%Y%m%dT%H%M%SZ).$ext"
tmp="$dir/.$name.partial"
trap 'rm -f "$tmp"' EXIT
[[ ! -e $dir/$name ]] || fail "$name already exists"

log "start → $dir/$name"
if [[ $kind == db ]]; then
  db=(); [[ $mode == compose ]] && db=(-d peace)
  pg pg_dump -Fc "${db[@]}" > "$tmp" || fail "pg_dump exited with an error"
  # Reading the whole archive back fails on any truncation that loses content (checked by sweeping cut points);
  # a PEACE dump always has the migration table.
  pg pg_restore --list < "$tmp" | grep 'TABLE DATA public alembic_version' > /dev/null || fail "dump has no PEACE schema"
  pg pg_restore -f /dev/null < "$tmp" || fail "dump cannot be read back in full"
else
  [[ -d $vault ]] || fail "vault not found: $vault"
  tar -C "$(dirname "$vault")" -czf "$tmp" "$(basename "$vault")" || fail "tar exited with an error"
  tar -tzf "$tmp" > /dev/null || fail "archive cannot be read back in full"  # gzip CRC covers every byte
fi
[[ -s $tmp ]] || fail "backup is empty"
printf '%s  %s\n' "$(sha256sum < "$tmp" | cut -d' ' -f1)" "$name" > "$dir/$name.sha256"
mv "$tmp" "$dir/$name"
log "ok: $name ($(du -h "$dir/$name" | cut -f1))"

# Off-machine copy: written as a hidden partial file, checked against the checksum there, then renamed.
if [[ -n ${PEACE_BACKUP_COPY_TO:-} ]]; then
  dest=$PEACE_BACKUP_COPY_TO
  hash=$(cut -d' ' -f1 "$dir/$name.sha256")
  if [[ $dest == *:* ]]; then
    remote=$(printf %q "${dest#*:}")
    ssh -o BatchMode=yes -- "${dest%%:*}" "umask 077 && mkdir -p $remote && cd $remote && test ! -e $name && cat > .$name.partial &&
      { printf '%s  %s\n' $hash .$name.partial | sha256sum -c --quiet - && mv .$name.partial $name && printf '%s  %s\n' $hash $name > $name.sha256 ||
        { rm -f .$name.partial; exit 1; }; }" < "$dir/$name" || fail "copy to $dest failed or does not match (the local backup is kept)"
  else
    mkdir -p "$dest"
    [[ ! -e $dest/$name ]] || fail "$dest/$name already exists; not replaced"
    if ! { cp -- "$dir/$name" "$dest/.$name.partial" && printf '%s  %s\n' "$hash" "$dest/.$name.partial" | sha256sum -c --quiet -; }; then
      rm -f "$dest/.$name.partial"
      fail "copy to $dest failed or does not match (the local backup is kept)"
    fi
    mv -- "$dest/.$name.partial" "$dest/$name"
    cp -- "$dir/$name.sha256" "$dest/$name.sha256"
  fi
  log "copied and verified at $dest"
else
  log "warning: PEACE_BACKUP_COPY_TO is not set; this backup is on this disk only"
fi

# Retention: only this kind's finished backups; names sort by time.
mapfile -t old < <(find "$dir" -maxdepth 1 -name "$prefix-*.$ext" -printf '%f\n' | sort | head -n "-$keep")
for f in "${old[@]}"; do
  rm -f "$dir/$f" "$dir/$f.sha256"
  log "retention: removed $f"
done
