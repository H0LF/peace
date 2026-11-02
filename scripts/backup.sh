#!/usr/bin/env bash
# Back up the PEACE database (`backup.sh db`), the Obsidian vault (`backup.sh vault`) or the private git
# repository with its whole history (`backup.sh repo`).
# A backup only gets its final name after it was read back in full and checksummed; a failed run leaves
# nothing behind and never touches older backups. Then it is restored into a throwaway place
# (scripts/restore-test.sh). Retention keeps the newest PEACE_BACKUP_KEEP of each kind.
# Every run, good or bad, ends by writing $PEACE_BACKUP_DIR/status-<kind>.json (read by /api/v1/diagnostics).
#
# PEACE_BACKUP_DIR   where backups go (default ~/peace-backups, mode 700, files 600)
# PEACE_BACKUP_KEEP  backups kept per kind (default 14)
# PEACE_BACKUP_MODE  db only: "compose" (default; pg tools inside the db container, no password needed)
#                    or "direct" (local pg tools with libpq settings: PGHOST, PGPORT, PGUSER, PGDATABASE,
#                    password in ~/.pgpass; never put a password on the command line)
# PEACE_COMPOSE      compose command (default "docker compose"), run from the repository root
# PEACE_VAULT        vault only: the vault directory (default ~/Peace Vault); it is only read
# PEACE_REPO         repo only: the git repository (default ~/peace); it is only read
# PEACE_BACKUP_RESTORE_TEST  1 (default) restores every new backup into a throwaway place; 0 skips it
# PEACE_BACKUP_COPY_TO  a second place for every new backup, off this disk: a directory (external drive) or
#                    host:directory over SSH (key auth). Verified there by checksum; copies there are never
#                    replaced or deleted. Unset: the backup exists on this disk only (logged as a warning).
# PEACE_BACKUP_GPG_RECIPIENT  full fingerprint of a GnuPG public key: the copy off this disk is encrypted to it
#                    (<name>.gpg). Only the public key is needed here; keep the private key off this disk too.
set -euo pipefail
umask 077
kind=${1:-}
dir=${PEACE_BACKUP_DIR:-$HOME/peace-backups}
keep=${PEACE_BACKUP_KEEP:-14}
mode=${PEACE_BACKUP_MODE:-compose}
read -r -a compose <<< "${PEACE_COMPOSE:-docker compose}"
vault=${PEACE_VAULT:-$HOME/Peace Vault}
repo=${PEACE_REPO:-$HOME/peace}
recipient=${PEACE_BACKUP_GPG_RECIPIENT:-}
scripts=$(cd "$(dirname "$0")" && pwd)
cd "$scripts/.."

name="" tmp="" offsite=none encrypted=false restore=skipped error=""
log() { printf '%s peace-backup[%s] %s\n' "$(date -u +%FT%TZ)" "$kind" "$*" >&2; }
fail() { error=$*; log "FAILED: $*"; exit 1; }
[[ $kind == db || $kind == vault || $kind == repo ]] || { echo "usage: $0 db|vault|repo" >&2; exit 2; }

mkdir -p "$dir"
chmod 700 "$dir"
status() {  # fixed strings only: no secrets or paths from the environment end up here
  local ok=false; [[ -z $error && $1 == 0 ]] && ok=true
  printf '{"kind":"%s","ok":%s,"finished_at":"%s","file":"%s","offsite":"%s","encrypted":%s,"restore_test":"%s","error":"%s"}\n' \
    "$kind" "$ok" "$(date -u +%FT%TZ)" "$name" "$offsite" "$encrypted" "$restore" "${error//\"/\'}" > "$dir/.status-$kind.json" \
    && mv "$dir/.status-$kind.json" "$dir/status-$kind.json"
}
trap 'code=$?; [[ -n $tmp ]] && rm -f "$tmp" "$tmp.gpg"; status $code' EXIT

[[ $keep =~ ^[1-9][0-9]*$ ]] || fail "PEACE_BACKUP_KEEP must be a positive integer"
[[ $mode == compose || $mode == direct ]] || fail "PEACE_BACKUP_MODE must be compose or direct"
[[ -z $recipient || $recipient =~ ^[0-9A-F]{40}$ ]] || fail "PEACE_BACKUP_GPG_RECIPIENT must be a full 40-character fingerprint"

# Run a PostgreSQL client tool inside the db container or locally.
pg() { if [[ $mode == compose ]]; then "${compose[@]}" exec -T db "$1" -U peace "${@:2}"; else "$@"; fi; }

exec 9> "$dir/.lock"
flock -n 9 || fail "another backup is running"

case $kind in db) prefix=peace ext=dump ;; vault) prefix=vault ext=tar.gz ;; repo) prefix=repo ext=bundle ;; esac
name="$prefix-$(date -u +%Y%m%dT%H%M%SZ).$ext"
tmp="$dir/.$name.partial"
[[ ! -e $dir/$name ]] || fail "$name already exists"

log "start → $dir/$name"
if [[ $kind == db ]]; then
  db=(); [[ $mode == compose ]] && db=(-d peace)
  pg pg_dump -Fc "${db[@]}" > "$tmp" || fail "pg_dump exited with an error"
  # Reading the whole archive back fails on any truncation that loses content (checked by sweeping cut points);
  # a PEACE dump always has the migration table.
  pg pg_restore --list < "$tmp" | grep 'TABLE DATA public alembic_version' > /dev/null || fail "dump has no PEACE schema"
  pg pg_restore -f /dev/null < "$tmp" || fail "dump cannot be read back in full"
elif [[ $kind == vault ]]; then
  [[ -d $vault ]] || fail "vault not found: $vault"
  tar -C "$(dirname "$vault")" -czf "$tmp" "$(basename "$vault")" || fail "tar exited with an error"
  tar -tzf "$tmp" > /dev/null || fail "archive cannot be read back in full"  # gzip CRC covers every byte
else
  git -C "$repo" rev-parse --git-dir > /dev/null 2>&1 || fail "not a git repository: $repo"
  git -C "$repo" bundle create "$tmp" --all 2> /dev/null || fail "git bundle exited with an error"
  git -C "$repo" bundle verify -q "$tmp" > /dev/null 2>&1 || fail "bundle cannot be verified"
fi
[[ -s $tmp ]] || fail "backup is empty"
printf '%s  %s\n' "$(sha256sum < "$tmp" | cut -d' ' -f1)" "$name" > "$dir/$name.sha256"
mv "$tmp" "$dir/$name"
log "ok: $name ($(du -h "$dir/$name" | cut -f1))"

if [[ ${PEACE_BACKUP_RESTORE_TEST:-1} == 1 ]]; then
  if "$scripts/restore-test.sh" "$dir/$name"; then restore=passed; else restore=failed; fail "restore test failed (the backup is kept)"; fi
fi

# Off-machine copy: written as a hidden partial file, checked against the checksum there, then renamed.
if [[ -n ${PEACE_BACKUP_COPY_TO:-} ]]; then
  dest=$PEACE_BACKUP_COPY_TO src="$dir/$name" out=$name
  if [[ -n $recipient ]]; then
    tmp="$dir/.$name"  # the trap removes $tmp.gpg
    gpg --batch --quiet --no-tty --trust-model always --recipient "$recipient" --output "$tmp.gpg" --encrypt "$dir/$name" 2> /dev/null \
      || fail "could not encrypt to $recipient (is its public key imported?)"
    src=$tmp.gpg out=$name.gpg encrypted=true
  else
    log "warning: PEACE_BACKUP_GPG_RECIPIENT is not set; the copy off this disk is not encrypted"
  fi
  hash=$(sha256sum < "$src" | cut -d' ' -f1)
  if [[ $dest == *:* ]]; then
    remote=$(printf %q "${dest#*:}")
    ssh -o BatchMode=yes -- "${dest%%:*}" "umask 077 && mkdir -p $remote && cd $remote && test ! -e $out && cat > .$out.partial &&
      { printf '%s  %s\n' $hash .$out.partial | sha256sum -c --quiet - && mv .$out.partial $out && printf '%s  %s\n' $hash $out > $out.sha256 ||
        { rm -f .$out.partial; exit 1; }; }" < "$src" || { offsite=failed; fail "copy to $dest failed or does not match (the local backup is kept)"; }
  else
    mkdir -p "$dest"
    [[ ! -e $dest/$out ]] || { offsite=failed; fail "$dest/$out already exists; not replaced"; }
    if ! { cp -- "$src" "$dest/.$out.partial" && printf '%s  %s\n' "$hash" "$dest/.$out.partial" | sha256sum -c --quiet -; }; then
      rm -f "$dest/.$out.partial"
      offsite=failed
      fail "copy to $dest failed or does not match (the local backup is kept)"
    fi
    mv -- "$dest/.$out.partial" "$dest/$out"
    printf '%s  %s\n' "$hash" "$out" > "$dest/$out.sha256"
  fi
  offsite=verified
  log "copied and verified at $dest${recipient:+ (encrypted)}"
else
  log "warning: PEACE_BACKUP_COPY_TO is not set; this backup is on this disk only"
fi

# Retention: only this kind's finished backups; names sort by time.
mapfile -t old < <(find "$dir" -maxdepth 1 -name "$prefix-*.$ext" -printf '%f\n' | sort | head -n "-$keep")
for f in "${old[@]}"; do
  rm -f "$dir/$f" "$dir/$f.sha256"
  log "retention: removed $f"
done
