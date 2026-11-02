# PEACE

Secretária académica pessoal que corre no teu PC. Junta o Moodle e o inforestudante (Nónio) num só sítio: aulas,
avaliações, prazos e materiais das cadeiras. Planeia o teu estudo, avisa-te antes dos prazos e responde por texto
ou por voz, em português.

Tudo fica no teu computador: a base de dados, os ficheiros das cadeiras e as tuas credenciais. Nada é enviado
para servidores de terceiros.

## O que faz

- **Sincronização** diária (02:00) e manual do Moodle e do inforestudante: cadeiras, horário, avaliações, entregas e
  materiais. Cada integração pode ser ligada ou desligada.
- **Painel** (no browser): hoje, calendário, prazos, tarefas, plano de estudo, materiais por cadeira e um grafo de
  conhecimento dos temas das cadeiras.
- **Planeamento**: sessões de estudo distribuídas pelos teus tempos livres, por ordem de prioridade.
- **Assistente**: «o que tenho amanhã?», «que prazos tenho esta semana?», «planeia a semana», «o que devo estudar
  agora?». Com um modelo local (opcional), também conversa e reescreve as respostas de forma natural, sem nunca
  mudar horas, datas ou números.
- **Notificações** no ambiente de trabalho (Linux) antes de aulas, avaliações e prazos.
- **Opcional**: voz (botão do microfone no browser, ou manter SUPER+Y premido e falar), ações no PC com Hyprland, separadores do Brave e cópia dos
  materiais para um cofre do Obsidian.

## Instalar (Windows 10/11)

Abre o **PowerShell** (menu Iniciar → escreve «PowerShell») e cola:

```powershell
irm https://raw.githubusercontent.com/H0LF/peace/main/install.ps1 | iex
```

É o único comando. O instalador:

1. **Analisa o PC** (placa gráfica, RAM, disco) e pergunta **que modelo de IA** queres, com uma recomendação e o
   tamanho de cada um: `gpt-oss:20b` (13 GB), `qwen3:8b` (5,2 GB), `qwen3:4b` (2,6 GB), `llama3.2:3b` (2 GB) ou nenhum.
   Enter ou 30 segundos sem resposta escolhem o recomendado. Só descarrega o que escolheres.
2. Instala o **WSL 2 e o Docker Desktop**, onde corre o servidor da PEACE (ao continuar, aceitas os
   [termos do Docker](https://www.docker.com/legal/docker-subscription-service-agreement)). O Windows pede autorização
   e, num PC que nunca os teve, um reinício; depois de voltares a entrar, a instalação **continua sozinha**.
3. Instala a **PEACE** (servidor, painel e voz) no Docker, e o **agente do PC** (notificações, voz com SUPER+Y, ações no
   PC), que arranca sozinho quando entras no Windows.
4. Abre o painel **já com a sessão iniciada**.

Depois, em **Definições → Integrações**, escreve o endereço da plataforma da tua universidade (Moodle e/ou Nónio),
o utilizador e a palavra-passe, e carrega em **Sincronizar**. Para atualizar a PEACE, corre o mesmo comando.

No Windows, as áreas de trabalho virtuais e as disposições de janelas guardadas não estão disponíveis (o Windows
não tem uma forma oficial de as controlar); tudo o resto funciona como no Linux.

## Instalar (Linux)

```sh
git clone https://github.com/H0LF/peace.git
cd peace
./install.sh
```

O instalador:

1. instala o que faltar (PostgreSQL, curl, Python 3, [uv](https://docs.astral.sh/uv/)), pedindo autorização antes de
   usar `sudo` (Arch, Debian/Ubuntu e Fedora);
2. cria a base de dados da PEACE (só tua, sem portas abertas) e os serviços do utilizador no systemd;
3. mostra o endereço do painel (http://127.0.0.1:8420) e o token para o primeiro acesso.

Depois, no painel, abre **Definições → Integrações**, escreve o endereço da plataforma da tua universidade
(Moodle e/ou Nónio), o utilizador e a palavra-passe, e carrega em **Sincronizar**. Em **Definições → Assistente**
escolhes o modelo (por omissão, o melhor que estiver instalado). A PEACE começa vazia: só vê as tuas cadeiras e os teus materiais depois desta sincronização.

Correr `./install.sh` outra vez é seguro: não apaga dados nem substitui configurações.

### Alternativa: Docker (qualquer sistema)

```sh
scripts/init-secrets.sh              # cria secrets/peace.env e secrets/db_password
docker compose up -d --build
grep PEACE_API_TOKEN secrets/peace.env  # token para o primeiro acesso a http://127.0.0.1:8420
```

Com Docker não há notificações nem atalho de voz no ambiente de trabalho (o agente do PC é só para Linux); o botão do
microfone no painel funciona.

## Configuração

Configura-se tudo no painel, em **Definições**: plataformas (endereço, login, ligar/desligar), modelo do
assistente, voz, notificações e aparência. Só o fuso horário fica em `~/.config/peace/peace.env`
(`PEACE_TIMEZONE`, por omissão `Europe/Lisbon`).

O agente do PC lê `~/.config/peace/desktop.env`: `PEACE_PTT` (atalho de voz), `PEACE_TTS` (motor de voz),
`PEACE_VAULT` (cofre do Obsidian, por omissão `~/Peace Vault`).

Para o correio reconhecer melhor a tua escola, junta a `peace.env` os domínios dela e os nomes dos seus serviços:
`PEACE_ACADEMIC_DOMAINS=escola.pt,universidade.pt` e `PEACE_MAIL_SERVICES=nome|outro`.

### Extras opcionais

- **Modelo local** ([Ollama](https://ollama.com)): `ollama pull gpt-oss:20b` (precisa de uma GPU com ~16 GB).
  Sem modelo, o assistente percebe os pedidos por palavras-chave e responde com frases fixas.
- **Voz**: já vem incluída no Windows (serviço `voice` do Docker: Whisper para ouvir, Supertonic para falar). No
  Linux: `uv sync --extra voice` e `uv run peace-voice` (ou o [VoiceStudio](https://github.com/debpalash/VoiceStudio)
  em `127.0.0.1:3900`). O atalho de voz (SUPER+Y) muda-se em Definições → Voz.
- **Decisões com o [Laya](https://huggingface.co/convaiinnovations/laya)** (experimental): `scripts/install-laya.sh`
  (Linux, ~2 GB, corre no CPU). Responde a escolhas, pontuações e sim/não em `POST /api/v1/decide`. Sem treino acerta
  pouco, por isso nenhuma função da PEACE depende dele.
- **Brave**: a extensão em `desktop/brave/extension` (brave://extensions → Modo de programador → «Carregar
  descompactada») permite à PEACE gerir separadores.

## Usar a PEACE noutro dispositivo

A PEACE fica instalada num só PC e os outros dispositivos (portátil, telemóvel, Windows, Linux...) usam-na pelo
browser: **não instalas nada de novo, não descarregas ficheiros duas vezes** e tudo o que fazes aparece nos dois
lados, porque só há uma base de dados. A ligação usa o [Tailscale](https://tailscale.com) (gratuito para uso pessoal),
uma rede privada só dos teus dispositivos: nenhuma porta fica aberta à internet.

No PC onde a PEACE está instalada, corre uma vez:

```sh
scripts/remote-access.sh                                                    # Linux
powershell -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\PEACE\app\scripts\remote-access.ps1"   # Windows
```

Mostra o endereço `https://…ts.net` e um link para iniciar sessão. No outro dispositivo, instala o Tailscale, entra
na mesma conta e abre esse link uma vez.

- Funciona: painel, ficheiros das cadeiras (abrem no browser) e voz (botão do microfone, com o microfone e as colunas
  do dispositivo onde estás).
- Continua no PC principal: as **ações no PC** («abre», «fecha janela») e as notificações do ambiente de trabalho.
- O PC principal tem de estar ligado e acordado: um portátil fechado ou em suspensão deixa de responder.

## Atualizar e desinstalar

```sh
git pull && ./install.sh && systemctl --user restart peace-api peace-worker peace-desktop
```

Desinstalar: `systemctl --user disable --now peace-api peace-worker peace-desktop peace-db`, apagar
`~/.config/systemd/user/peace-*.service`, `~/.config/peace` e `~/.local/share/peace` (os teus dados).

## Privacidade e segurança

- O painel só aceita ligações deste PC (`127.0.0.1`) e pede um token. Com `remote-access`, o Tailscale deixa entrar
  também os dispositivos da tua conta: não uses o Tailscale Funnel, que abriria a PEACE à internet.
- A base de dados só aceita ligações locais, sem palavra-passe nem porta de rede.
- As credenciais das integrações ficam na base de dados local e nunca são mostradas de volta no painel. As cópias
  de segurança (`scripts/backup.sh`) incluem-nas: guarda-as num sítio seguro.
- O conteúdo do Moodle e do inforestudante é tratado como dados, nunca como instruções para o assistente.

## Desenvolver

```sh
uv sync
# Os testes precisam de um PostgreSQL descartável com uma base de dados *_test (o esquema é apagado):
export PEACE_DATABASE_URL=postgresql+psycopg://peace@127.0.0.1:55432/peace_test
uv run pytest
cd dashboard && npm ci && npm run build   # painel; testes no browser: npx playwright test
```

## Licença

[MIT](LICENSE)
