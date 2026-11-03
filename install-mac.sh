#!/usr/bin/env bash
# PEACE on a Mac, in one line in the Terminal:
#   curl -fsSL https://raw.githubusercontent.com/H0LF/peace/main/install-mac.sh | bash
#
# Looks at this Mac and lets you choose the AI model (with a recommendation), installs what is missing (Docker Desktop,
# Ollama), starts PEACE in Docker and opens it signed in. macOS asks for your password once, for Docker.
# Safe to run again: it updates PEACE and keeps your data and settings. To remove it: ~/.peace/app/scripts/uninstall-mac.sh
set -euo pipefail

REPO=https://github.com/H0LF/peace
HOME_DIR=$HOME/.peace  # no spaces: Docker's env files and bind mounts are simpler that way
APP=$HOME_DIR/app
SECRETS=$HOME_DIR/secrets
AGENTS=$HOME/Library/LaunchAgents

say() { printf '\n\033[36m==> %s\033[0m\n' "$1"; }
die() { printf '\n\033[31m%s\033[0m\n' "$1" >&2; exit 1; }

# A Mac's memory is shared with its graphics, so it is all one number. Same models as llm.PREFERRED and install.ps1.
# Prints the menu's lines: number|model|size|description|fits.
choices() {
  local ram=$1 spare=$(($2 - 15))  # ~15 GB for Docker, PEACE and the voice models
  local n=0 model size label need fits whole
  while IFS='|' read -r model size label need; do
    n=$((n + 1))
    fits=no
    whole=${size%.*}
    [ "$whole" = "$size" ] || whole=$((whole + 1))  # 2.6 GB needs 3
    if [ "$ram" -ge "$need" ] && { [ -z "$model" ] || [ "$whole" -le "$spare" ]; }; then fits=yes; fi
    printf '%s|%s|%s|%s|%s\n' "$n" "$model" "$size" "$label" "$fits"
  done <<LIST
gpt-oss:20b|13|o melhor; precisa de 32 GB de memória|32
qwen3:8b|5.2|muito bom; 16 GB de memória|16
qwen3:4b|2.6|equilibrado; 12 GB de memória|12
llama3.2:3b|2|leve, para Macs modestos; 8 GB de memória|8
|0|sem modelo: a PEACE responde com frases fixas (podes ligar um modelo na nuvem depois)|0
LIST
}

recommended() { choices "$1" "$2" | awk -F'|' '$5 == "yes" { print $1; exit }'; }

pick_model() {
  local ram=$1 free=$2 best answer="" n model size label fits mark
  best=$(recommended "$ram" "$free")
  say "Este Mac tem $ram GB de memória e $free GB livres. Escolhe o modelo de IA:"
  while IFS='|' read -r n model size label fits; do
    mark=""
    [ "$n" = "$best" ] && mark=' (recomendado)'
    [ "$fits" = yes ] || mark=' (pesado para este Mac)'
    printf '  %s) %s%s: %s%s\n' "$n" "${model:-Nenhum}" "$([ -n "$model" ] && echo ", $size GB")" "$label" "$mark"
  done < <(choices "$ram" "$free")
  # stdin is this script under curl | bash: the keyboard is the terminal. Enter or 30 s: the recommendation.
  if [ -r /dev/tty ]; then
    printf 'Número [%s]: ' "$best" > /dev/tty
    read -r -t 30 -n 1 answer < /dev/tty || true
    printf '\n' > /dev/tty
  fi
  case "$answer" in [1-5]) ;; *) answer=$best ;; esac
  MODEL=$(choices "$ram" "$free" | awk -F'|' -v n="$answer" '$1 == n { print $2 }')
}

secret() { LC_ALL=C tr -dc 'A-Za-z0-9_-' < /dev/urandom | head -c 43; }
wait_for() {  # wait_for seconds command...
  local until=$((SECONDS + $1)); shift
  until "$@" > /dev/null 2>&1; do
    [ "$SECONDS" -lt "$until" ] || return 1
    sleep 3
  done
}

install_docker() {
  export PATH="$PATH:/usr/local/bin:$HOME/.docker/bin:/Applications/Docker.app/Contents/Resources/bin"
  if [ ! -d /Applications/Docker.app ]; then
    local arch=amd64 dmg
    [ "$(uname -m)" = arm64 ] && arch=arm64
    say 'A instalar o Docker Desktop (cerca de 1 GB)…'
    echo 'Ao continuar aceitas os termos do Docker: https://www.docker.com/legal/docker-subscription-service-agreement/'
    echo 'É gratuito para uso pessoal e educativo. O macOS vai pedir a tua palavra-passe uma vez.'
    dmg=$(mktemp -d)/Docker.dmg
    curl -fL --progress-bar -o "$dmg" "https://desktop.docker.com/mac/main/$arch/Docker.dmg"
    hdiutil attach -quiet -nobrowse -mountpoint /Volumes/PEACE-Docker "$dmg"
    sudo /Volumes/PEACE-Docker/Docker.app/Contents/MacOS/install --accept-license --user="$USER" < /dev/tty \
      || { hdiutil detach -quiet /Volumes/PEACE-Docker; die 'O Docker Desktop não ficou instalado. Volta a correr este comando.'; }
    hdiutil detach -quiet /Volumes/PEACE-Docker
  fi
  if ! docker info > /dev/null 2>&1; then
    say 'A arrancar o Docker Desktop (na primeira vez demora um ou dois minutos)…'
    open -g -a Docker
    wait_for 300 docker info || die 'O Docker não arrancou. Abre a aplicação Docker, espera que fique pronta e volta a correr este comando.'
  fi
}

install_ollama() {
  [ -n "$MODEL" ] || return 0
  local zip line done_b total
  if [ ! -d /Applications/Ollama.app ]; then
    say 'A instalar o Ollama (corre a IA diretamente no Mac, com a placa gráfica)…'
    zip=$(mktemp -d)/Ollama.zip
    curl -fL --progress-bar -o "$zip" https://ollama.com/download/Ollama-darwin.zip
    ditto -xk "$zip" /Applications
  fi
  curl -fs --max-time 2 http://127.0.0.1:11434/api/tags > /dev/null 2>&1 || open -g -a Ollama
  wait_for 90 curl -fs --max-time 2 http://127.0.0.1:11434/api/tags || die 'O Ollama não arrancou. Abre a aplicação Ollama e volta a correr este comando.'
  say "A descarregar o modelo $MODEL (pode demorar bastante)…"
  curl -fsN http://127.0.0.1:11434/api/pull -d "{\"name\":\"$MODEL\"}" | while IFS= read -r line; do
    case "$line" in *'"error"'*) printf '%s\n' "$line" >&2; exit 1 ;; esac
    done_b=$(printf '%s' "$line" | sed -n 's/.*"completed":\([0-9]*\).*/\1/p')
    total=$(printf '%s' "$line" | sed -n 's/.*"total":\([0-9]*\).*/\1/p')
    [ -z "$done_b" ] || [ -z "$total" ] || printf '\r  %3d%%' $((done_b / (total / 100 + 1)))
  done
  printf '\n'
}

install_peace() {
  local unpacked env=$SECRETS/peace.env password=$SECRETS/db_password kept
  say 'A descarregar a PEACE…'
  mkdir -p "$SECRETS"
  chmod 700 "$HOME_DIR" "$SECRETS"
  unpacked=$(mktemp -d)
  curl -fsSL "$REPO/archive/refs/heads/main.tar.gz" | tar -xz -C "$unpacked" --strip-components=1
  rm -rf "$APP"  # only code: data lives in Docker volumes and in $SECRETS
  mv "$unpacked" "$APP"

  [ -f "$password" ] || { secret > "$password"; chmod 600 "$password"; }
  grep -qs '^PEACE_API_TOKEN=' "$env" || { touch "$env"; chmod 600 "$env"; echo "PEACE_API_TOKEN=$(secret)" >> "$env"; }
  # The model chosen now, even if a bigger one is already here. Settings > IA e modelos can still change it.
  kept=$(grep -vE '^PEACE_(LLM_MODEL|INSTALL_KIND)=' "$env" || true)
  { printf '%s\n' "$kept"; echo 'PEACE_INSTALL_KIND=docker'; [ -z "$MODEL" ] || echo "PEACE_LLM_MODEL=$MODEL"; } > "$env"
  printf 'PEACE_ENV_FILE=%s\nPEACE_DB_PASSWORD_FILE=%s\nPEACE_STT_MODEL=small\n' "$env" "$password" > "$APP/.env"

  say 'A preparar os serviços da PEACE no Docker (na primeira vez demora vários minutos)…'
  (cd "$APP" && docker compose up -d --build) || die 'O Docker não conseguiu arrancar a PEACE. Vê as mensagens acima.'
  wait_for 300 curl -fs --max-time 3 http://127.0.0.1:8420/health || die "A PEACE não respondeu. Vê o estado com: docker compose -f $APP/compose.yaml logs"
}

agent() {  # agent name interval-or-empty program args...: a job that macOS starts at login
  local name=$1 every=$2 plist args="" a
  shift 2
  plist=$AGENTS/com.peace.$name.plist
  for a in "$@"; do args="$args<string>$a</string>"; done
  mkdir -p "$AGENTS"
  cat > "$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.peace.$name</string>
<key>ProgramArguments</key><array>$args</array>
<key>RunAtLoad</key><true/>
${every:+<key>StartInterval</key><integer>$every</integer>}
<key>EnvironmentVariables</key><dict><key>PATH</key><string>/usr/bin:/bin:/usr/sbin:/usr/local/bin:$HOME/.docker/bin:/Applications/Docker.app/Contents/Resources/bin</string></dict>
</dict></plist>
PLIST
  launchctl unload "$plist" 2> /dev/null || true
  launchctl load "$plist"
}

main() {
  [ "$(uname -s)" = Darwin ] || die 'Este instalador é para macOS. Em Linux usa ./install.sh'
  local ram free token
  ram=$(($(sysctl -n hw.memsize) / 1073741824))
  free=$(df -g "$HOME" | awk 'NR == 2 { print $4 }')
  [ "$free" -ge 15 ] || die "Só tens $free GB livres; a PEACE e o Docker precisam de pelo menos 15 GB."
  pick_model "$ram" "$free"
  install_docker
  install_ollama
  install_peace
  agent start '' /usr/bin/open -g -a Docker  # PEACE's services restart with Docker
  agent update 900 "$APP/scripts/update.sh"  # Settings > Geral decides whether an update installs

  token=$(sed -n 's/^PEACE_API_TOKEN=//p' "$SECRETS/peace.env")
  printf '[InternetShortcut]\nURL=http://127.0.0.1:8420\n' > "$HOME/Desktop/PEACE.url"
  open "http://127.0.0.1:8420/entrar#token=$token"
  printf '\n\033[32mA PEACE está instalada e aberta no browser.\033[0m\n'
  echo 'Em Definições > Integrações escreve o endereço da plataforma da tua universidade, o utilizador e a palavra-passe, e carrega em «Sincronizar».'
  echo 'Há um atalho «PEACE» na tua secretária. O microfone funciona no browser: autoriza-o quando ele pedir.'
  echo 'Para remover a PEACE: ~/.peace/app/scripts/uninstall-mac.sh'
}

[ -n "${PEACE_INSTALLER_TEST:-}" ] || main
