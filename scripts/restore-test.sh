#!/usr/bin/env bash
# Prove a backup can be restored, without touching anything real:
#   restore-test.sh <peace-*.dump | vault-*.tar.gz | repo-*.bundle>
# db:    a brand-new PostgreSQL cluster in a temporary directory (Unix socket only, no TCP), restored in one
#        transaction; prints the migration and row counts. The running PEACE databases are never contacted.
# vault: extracted into a temporary directory, then `tar --compare` checks every file's contents, size, mode
#        and modification time against the archive.
# repo:  cloned from the bundle into a temporary directory and checked with `git fsck`.
# The temporary place is always removed. Exit 0 only when everything checked out.
set -euo pipefail
umask 077
file=$(realpath "${1:?usage: restore-test.sh <backup file>}")
log() { printf '%s peace-restore-test %s\n' "$(date -u +%FT%TZ)" "$*" >&2; }
fail() { log "FAILED: $*"; exit 1; }

[[ -f $file.sha256 ]] || fail "no checksum file next to the backup"
(cd "$(dirname "$file")" && sha256sum --quiet -c "$(basename "$file").sha256") || fail "checksum mismatch"
work=$(mktemp -d /var/tmp/peace-restore-test.XXXXXX)  # on disk (a vault is large) and short (socket path limit)
cleanup() { [[ -f $work/data/postmaster.pid ]] && pg_ctl -D "$work/data" -m immediate stop > /dev/null 2>&1; rm -rf "$work"; }
trap cleanup EXIT

case $file in
  *.dump)
    command -v initdb > /dev/null && command -v pg_ctl > /dev/null || fail "initdb/pg_ctl not on PATH; cannot make a throwaway cluster"
    initdb -D "$work/data" -U peace --auth=trust -E UTF8 > /dev/null || fail "initdb failed"
    pg_ctl -D "$work/data" -o "-c listen_addresses= -k $work" -l "$work/log" -w start > /dev/null || fail "throwaway cluster did not start"
    q() { psql -h "$work" -U peace -d peace -XAtc "$1"; }
    createdb -h "$work" -U peace peace || fail "createdb failed"
    pg_restore -h "$work" -U peace -d peace --exit-on-error --single-transaction --no-owner < "$file" 2> "$work/err" \
      || fail "pg_restore: $(head -c 300 "$work/err")"
    counts=$(q "SELECT 'courses=' || (SELECT count(*) FROM courses) || ' events=' || (SELECT count(*) FROM events)
                || ' tasks=' || (SELECT count(*) FROM tasks) || ' materials=' || (SELECT count(*) FROM materials)") || fail "restored database cannot be queried"
    log "ok: $(basename "$file") restored into a throwaway cluster (migration $(q 'SELECT version_num FROM alembic_version'), $counts)"
    ;;
  *.tar.gz)
    tar -C "$work" -xpzf "$file" || fail "extraction failed"
    tar -C "$work" --compare -zf "$file" > "$work/diff" 2>&1 || fail "extracted files differ from the archive: $(head -c 300 "$work/diff")"
    log "ok: $(basename "$file") extracted and compared ($(tar -tzf "$file" | grep -vc '/$') files; contents, sizes, modes, times)"
    ;;
  *.bundle)
    git clone -q --mirror "$file" "$work/repo" 2> "$work/err" || fail "clone failed: $(head -c 300 "$work/err")"
    git -C "$work/repo" fsck --no-dangling 2> "$work/err" > /dev/null || fail "fsck: $(head -c 300 "$work/err")"
    log "ok: $(basename "$file") cloned and fsck clean ($(git -C "$work/repo" rev-list --all --count) commits, $(git -C "$work/repo" for-each-ref | wc -l) refs)"
    ;;
  *) fail "unknown backup kind: $(basename "$file")" ;;
esac
