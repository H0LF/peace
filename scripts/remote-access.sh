#!/usr/bin/env bash
# Use PEACE from your other devices (laptop, phone), with everything staying on this computer.
# Tailscale gives this PC a private https address only your own devices can reach: no port is opened to the
# internet and PEACE keeps listening on 127.0.0.1. Https is also what lets browsers use the microphone.
# Safe to run again. To stop: tailscale serve --https=443 off
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)

if ! command -v tailscale > /dev/null; then
  echo "Instala primeiro o Tailscale neste PC: https://tailscale.com/download" >&2
  exit 1
fi
state() { tailscale status --json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["BackendState"])' || true; }
if [ "$(state)" != Running ]; then
  echo "A ligar este PC ao Tailscale: abre o link que aparecer e entra na tua conta."
  sudo tailscale up
fi
# Without the operator setting, serve needs root.
tailscale serve --bg 8420 || sudo tailscale serve --bg 8420  # may print a link to enable Serve, then waits for it

host=$(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')
token=$(grep -hm1 '^PEACE_API_TOKEN=' ~/.config/peace/peace.env "$repo/secrets/peace.env" 2>/dev/null | head -1 | cut -d= -f2-)

cat <<EOF

A PEACE está disponível nos teus dispositivos em:
  https://$host

Em cada dispositivo: instala o Tailscale (https://tailscale.com/download), entra na MESMA conta e abre este link
uma vez para iniciar sessão. O link contém a tua chave de acesso: não o partilhes.
  https://$host/entrar#token=$token

Este PC tem de estar ligado e acordado. Se o Tailscale pedir para ativar HTTPS, aceita o link que ele mostrar.
EOF
