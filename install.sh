#!/usr/bin/env bash
# PEACE on Linux in one command: ./install.sh  (on a Mac: install-mac.sh)
# Installs what is missing (asks before using sudo), then sets PEACE up as systemd user services
# (desktop/install-local.sh). Everything else is set in the dashboard. Safe to run again; nothing is replaced.
set -euo pipefail
cd "$(dirname "$0")"
repo=$(pwd)
conf=~/.config/peace

ask() {  # ask "question" default -> answer (the default when not interactive)
  local answer=""
  [ -t 0 ] && read -r -p "$1 " answer
  echo "${answer:-$2}"
}

# 1. System packages: PostgreSQL (binaries only; PEACE runs its own cluster), curl, Python 3 for the desktop agent,
# bubblewrap for the sandbox that grades code and runs assignment checks.
missing=()
for dir in /usr/lib/postgresql/*/bin; do [ -x "$dir/initdb" ] && PATH="$dir:$PATH"; done
command -v initdb > /dev/null || missing+=(postgresql)
command -v curl > /dev/null || missing+=(curl)
command -v python3 > /dev/null || missing+=(python3)
command -v bwrap > /dev/null || missing+=(bubblewrap)
if ((${#missing[@]})); then
  if command -v pacman > /dev/null; then cmd=(sudo pacman -S --needed postgresql curl python bubblewrap)
  elif command -v apt-get > /dev/null; then cmd=(sudo apt-get install -y postgresql curl python3 bubblewrap)
  elif command -v dnf > /dev/null; then cmd=(sudo dnf install -y postgresql-server curl python3 bubblewrap)
  else echo "Install these first: ${missing[*]}" >&2; exit 1
  fi
  echo "Missing: ${missing[*]}"
  [ "$(ask "Run '${cmd[*]}' now? [Y/n]" y)" != n ] || { echo "Install them and run ./install.sh again." >&2; exit 1; }
  "${cmd[@]}"
fi

# 2. uv: installs the right Python and PEACE's dependencies into .venv.
if ! command -v uv > /dev/null && [ ! -x ~/.local/bin/uv ]; then
  [ "$(ask "uv (Python package manager) is missing. Install it from astral.sh? [Y/n]" y)" != n ] || exit 1
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
PATH=~/.local/bin:$PATH
uv sync --frozen --no-dev

# 3. Dashboard: prebuilt in dashboard/dist; rebuilt only if missing (needs Node.js 20+).
if [ ! -f dashboard/dist/index.html ]; then
  (cd dashboard && npm ci --no-audit --no-fund && npm run build)
fi

# 4. Database, services, desktop agent.
desktop/install-local.sh

# 5. Updates: Settings > Geral decides; this timer asks PEACE every 15 minutes whether one is due.
grep -qs '^PEACE_INSTALL_KIND=' "$conf/peace.env" || { echo 'PEACE_INSTALL_KIND=linux' >> "$conf/peace.env"; systemctl --user restart peace-api peace-worker; }
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/peace-update.service <<UNIT
[Unit]
Description=PEACE: install a new version when one is due
[Service]
Type=oneshot
ExecStart=$repo/scripts/update.sh
UNIT
cat > ~/.config/systemd/user/peace-update.timer <<UNIT
[Unit]
Description=PEACE: ask whether a new version is due
[Timer]
OnBootSec=5min
OnUnitActiveSec=15min
[Install]
WantedBy=timers.target
UNIT
systemctl --user daemon-reload
systemctl --user enable --now peace-update.timer

token=$(grep -m1 '^PEACE_API_TOKEN=' "$conf/peace.env" | cut -d= -f2-)
cat <<EOF

PEACE is installed in $repo.
  Open:   http://127.0.0.1:8420
  Token:  $token   (the first login asks for it; it is also in $conf/peace.env)
Then, in Definições → Integrações, enter your university's addresses and logins and press «Sincronizar».
To use PEACE from a laptop or phone too: scripts/remote-access.sh
EOF
