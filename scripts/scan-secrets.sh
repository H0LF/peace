#!/usr/bin/env bash
# Look for this PC's live secrets (the values in PEACE's env files) and for key-shaped strings:
#   scan-secrets.sh --staged      what is about to be committed (the pre-commit hook)
#   scan-secrets.sh --history     every commit of every branch
#   scan-secrets.sh <directory>   a directory (scripts/export-public.sh runs it on the public copy)
# Prints the names of the keys whose values were found, never the values. Exit 1 when anything was found.
# Install as a hook: ln -sf ../../scripts/scan-secrets.sh .git/hooks/pre-commit  (it then scans --staged)
set -euo pipefail
target=${1:---staged}
[[ $(basename "$0") == pre-commit ]] && target=--staged
cd "$(dirname "$(realpath "$0")")/.."

text=$(mktemp)
trap 'rm -f "$text"' EXIT
case $target in
  --staged) git diff --cached --no-color -U0 > "$text" ;;
  --history) git log -p --all --no-color > "$text" ;;
  *) [[ -d $target ]] || { echo "usage: $0 --staged|--history|<directory>" >&2; exit 2; }
     grep -rIh -- '' "$target" > "$text" || true ;;
esac

found=0
keys="(TOKEN|PASSWORD|SECRET|KEY)$"
[[ -d $target ]] && keys="(TOKEN|PASSWORD|SECRET|KEY|USERNAME)$"  # a public copy must not name the owner either
for env in ~/.config/peace/peace.env ~/.config/peace/desktop.env ~/.config/peace/backup.env ~/.config/peace-dev/peace.env; do
  [[ -f $env ]] || continue
  while IFS='=' read -r key value; do
    key=${key#export } value=${value//\"/} value=${value//\'/}
    [[ $key =~ $keys && ${#value} -ge 6 ]] || continue
    if grep -qF -- "$value" "$text"; then echo "FOUND the value of $key ($(basename "$env"))" >&2; found=1; fi
  done < "$env"
done
# Shapes of secrets that are not in the env files: private keys, Moodle tokens in URLs, GitHub tokens.
if grep -qE -- '-----BEGIN [A-Z ]*PRIVATE KEY-----|[?&](ws)?token=[0-9a-f]{32}|gh[pousr]_[A-Za-z0-9]{36}' "$text"; then
  echo "FOUND a private key or an access token" >&2; found=1
fi
((found == 0)) || echo "secret scan of $target: FAILED (nothing printed; look for the keys named above)" >&2
exit $found
