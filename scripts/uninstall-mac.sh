#!/usr/bin/env bash
# Removes PEACE from this Mac. Asks before removing your data. Docker Desktop and Ollama stay: other programs may use them.
set -uo pipefail
home=$HOME/.peace
for job in start update; do
  plist=$HOME/Library/LaunchAgents/com.peace.$job.plist
  launchctl unload "$plist" 2> /dev/null
  rm -f "$plist"
done
printf 'Apagar também os teus dados da PEACE (tarefas, ficheiros, definições e chaves)? Não dá para desfazer. [s/N] '
read -r answer < /dev/tty
if [ "$answer" = s ] || [ "$answer" = S ]; then
  (cd "$home/app" && docker compose down --volumes)
  rm -rf "$home"
  echo 'A PEACE e os teus dados foram removidos.'
else
  (cd "$home/app" && docker compose down)
  rm -rf "$home/app" "$home/app.previous" "$home/app.failed"
  echo "A PEACE foi removida. Os teus dados continuam nos volumes do Docker e em $home/secrets."
fi
rm -f "$HOME/Desktop/PEACE.url"
