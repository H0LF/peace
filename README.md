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
- **Opcional**: voz (manter SUPER+Y premido e falar), ações no PC com Hyprland, separadores do Brave e cópia dos
  materiais para um cofre do Obsidian.

## Instalar (Windows 10/11)

Abre o **PowerShell** (menu Iniciar → escreve «PowerShell») e cola:

```powershell
irm https://raw.githubusercontent.com/H0LF/peace/main/install.ps1 | iex
```

O instalador trata de tudo o que faltar e pede autorização ao Windows quando é preciso:

1. **WSL 2 e Docker Desktop** (onde corre o servidor da PEACE). Num PC que nunca os teve, o Windows pede para
   reiniciar ou terminar a sessão; depois de voltares a entrar, a instalação **continua sozinha**. Na primeira vez
   que o Docker Desktop abre, aceita os termos dele.
2. **Ollama** e o **modelo** que o teu PC aguenta: vê a memória da placa gráfica e a RAM e escolhe
   `gpt-oss:20b` (placa gráfica com ~16 GB), `qwen3:8b`, `qwen3:4b` ou `llama3.2:3b` (portáteis mais modestos).
3. A **PEACE** (servidor, painel e voz) no Docker, e o **agente do PC** (notificações, voz com SUPER+Y,
   ações no PC), que arranca sozinho quando entras no Windows.
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

Com Docker não há notificações nem voz no ambiente de trabalho (o agente do PC é só para Linux).

## Configuração

Configura-se tudo no painel, em **Definições**: plataformas (endereço, login, ligar/desligar), modelo do
assistente, voz, notificações e aparência. Só o fuso horário fica em `~/.config/peace/peace.env`
(`PEACE_TIMEZONE`, por omissão `Europe/Lisbon`).

O agente do PC lê `~/.config/peace/desktop.env`: `PEACE_PTT` (atalho de voz), `PEACE_TTS` (motor de voz),
`PEACE_VAULT` (cofre do Obsidian, por omissão `~/Peace Vault`).

### Extras opcionais

- **Modelo local** ([Ollama](https://ollama.com)): `ollama pull gpt-oss:20b` (precisa de uma GPU com ~16 GB).
  Sem modelo, o assistente percebe os pedidos por palavras-chave e responde com frases fixas.
- **Voz**: já vem incluída no Windows (serviço `voice` do Docker: Whisper para ouvir, Supertonic para falar). No
  Linux: `uv sync --extra voice` e `uv run peace-voice` (ou o [VoiceStudio](https://github.com/debpalash/VoiceStudio)
  em `127.0.0.1:3900`). O atalho de voz (SUPER+Y) muda-se em Definições → Voz.
- **Brave**: a extensão em `desktop/brave/extension` (brave://extensions → Modo de programador → «Carregar
  descompactada») permite à PEACE gerir separadores.

## Atualizar e desinstalar

```sh
git pull && ./install.sh && systemctl --user restart peace-api peace-worker peace-desktop
```

Desinstalar: `systemctl --user disable --now peace-api peace-worker peace-desktop peace-db`, apagar
`~/.config/systemd/user/peace-*.service`, `~/.config/peace` e `~/.local/share/peace` (os teus dados).

## Privacidade e segurança

- O painel só aceita ligações deste PC (`127.0.0.1`) e pede um token.
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
