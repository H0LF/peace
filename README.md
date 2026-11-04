# PEACE

Secretária académica pessoal que corre no teu PC. Junta o Moodle e o inforestudante (Nónio) num só sítio: aulas,
avaliações, prazos e materiais das cadeiras. Planeia o teu estudo, avisa-te antes dos prazos e responde por texto
ou por voz, em português.

Tudo fica no teu computador: a base de dados, os ficheiros das cadeiras e as tuas credenciais. Com o modelo local,
as tuas perguntas e os teus ficheiros não saem do PC. Só saem dados se ligares um fornecedor de IA na nuvem (ChatGPT,
OpenAI, OpenRouter), o Google Calendar ou os avisos no telemóvel, e és tu que decides o quê: vê
[Privacidade](#privacidade).

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
- **Calendário sem repetições**: as sessões de estudo da mesma cadeira separadas só por uma pausa aparecem como um único
  bloco, e abri-lo mostra as partes e os temas.
- **O nome é teu**: em **Definições → Aparência** dás outro nome ao assistente e dizes «Jarvis, o que tenho amanhã?».
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

## Instalar (Mac)

Abre o **Terminal** (Cmd+Espaço → escreve «Terminal») e cola:

```sh
curl -fsSL https://raw.githubusercontent.com/H0LF/peace/main/install-mac.sh | bash
```

É o único comando, e é gratuito. Vê a memória e o disco do Mac e pergunta que modelo de IA queres, com uma recomendação;
instala o **Docker Desktop** (o macOS pede a tua palavra-passe uma vez; ao continuar aceitas os
[termos do Docker](https://www.docker.com/legal/docker-subscription-service-agreement)) e o **Ollama**, arranca a PEACE e
abre-a com a sessão iniciada. Funciona em Macs Apple Silicon e Intel. O microfone funciona no browser; o agente do PC
(notificações nativas, atalho de voz, ações no PC) ainda não existe para Mac.

Para remover: `~/.peace/app/scripts/uninstall-mac.sh` (pergunta antes de apagar os teus dados).

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

Com esta alternativa não há notificações nem atalho de voz no ambiente de trabalho (o agente do PC só é instalado pelos
instaladores de Linux e Windows); o botão do microfone no painel funciona, e as vozes ouvem-se no browser.

## Configuração

Configura-se tudo no painel, em **Definições**: plataformas (endereço, login, ligar/desligar), modelo do
assistente, voz, notificações e aparência. Só o fuso horário fica em `~/.config/peace/peace.env`
(`PEACE_TIMEZONE`, por omissão `Europe/Lisbon`).

O agente do PC lê `~/.config/peace/desktop.env`: `PEACE_PTT` (atalho de voz), `PEACE_TTS` (motor de voz),
`PEACE_VAULT` (cofre do Obsidian, por omissão `~/Peace Vault`).

Para o correio reconhecer melhor a tua escola, junta a `peace.env` os domínios dela e os nomes dos seus serviços:
`PEACE_ACADEMIC_DOMAINS=escola.pt,universidade.pt` e `PEACE_MAIL_SERVICES=nome|outro`.

### Escolher a IA

Em **Definições → IA e modelos** podes usar o modelo deste PC ou ligar a **OpenAI** ou o **OpenRouter** com a tua própria
chave de API; o modelo muda-se depois no canto do Início, ou a falar («muda para o GPT», «qual é o teu cérebro?», «volta
ao modelo anterior»). A chave confirma-se antes de ser guardada, nunca é mostrada de volta e fica no porta-chaves do
sistema ou, onde não houver (Docker), num ficheiro encriptado. Pagas ao fornecedor: uma subscrição do ChatGPT Plus ou Pro
não inclui crédito de API, mas podes entrar com ela diretamente, sem chave, e usá-la como fornecedor. Atenção: esta ligação não é oficial da OpenAI, pode deixar de funcionar a qualquer momento e usas a tua conta por tua conta e risco. Em **Definições → Privacidade e dados** decides o que pode sair do PC: a conversa e os documentos
que pedes vão por omissão; a leitura automática e o email só se os ligares.

### Privacidade

- **Chaves e sessões** nunca são mostradas de volta nem escritas nos registos. O painel só escuta em `127.0.0.1`.
- **Com um fornecedor na nuvem**, o que escolheres em Definições → Privacidade e dados segue para esse fornecedor, sujeito às
  regras dele: a conversa e os documentos que pedes vão por omissão (incluindo, quando a pergunta precisa, o teu horário,
  prazos e tarefas, e o texto inteiro dos ficheiros em «analisa todos os meus ficheiros»); a leitura automática e o email só
  se os ligares. Com o modelo local, nada disto sai do PC.
- **Avisos no telemóvel** (desligados por omissão) passam pelo servidor ntfy; **Google Calendar** só se o ligares.
- A PEACE só contacta o GitHub para ver se há versão nova. Não há telemetria.

### Modos e idioma

A PEACE abre com o dia, os prazos, o estudo e as cadeiras na barra. Em Definições → Funcionalidades escolhes o que aparece, e
as outras páginas estão sempre em «Todas as páginas». O idioma, português ou inglês, escolhe-se em Definições → Geral. A voz
continua em português.

### Planos de estudo

Diz «cria um plano de estudo para o exame de BD», «tenho exame de RC na sexta, ajuda-me a preparar», «tenho tempo
suficiente?» ou «perdi a sessão de ontem», ou usa a página **Estudo**. O plano usa o guia lido dos ficheiros, os conceitos,
os temas dos ficheiros ou, sem nada disso, um valor por defeito, e diz sempre em que se baseou. Adapta-se quando a data, o
calendário, o guia ou o teu progresso mudam. Para o ver no Google Calendar, liga «sessões de estudo» em Definições → Integrações.

### Extras opcionais

- **Modelo local** ([Ollama](https://ollama.com)): `ollama pull gpt-oss:20b` (precisa de uma GPU com ~16 GB).
  Sem modelo, o assistente percebe os pedidos por palavras-chave e responde com frases fixas.
- **Voz**: já vem incluída no Windows, no Mac e na alternativa Docker (serviço `voice`: Whisper para ouvir, Piper para
  falar em português europeu, tudo no CPU do teu PC, sem usar a GPU). Em Definições → Voz ouves e escolhes as vozes, mesmo sem o agente do PC. No Linux: `uv sync --extra voice` e `uv run peace-voice`. Vozes: Tugão (masculina, dados CC0), e Miro (masculina) e Dii (feminina), estas de TigreGotico Lda, CC BY-NC-ND 4.0, só para uso não
  comercial. Em inglês: LJSpeech, Cori, Joe e Mike (domínio público ou CC0). Descarregam-se uma vez. O `piper-tts` é GPL-3.0; o resto da PEACE é MIT. O atalho de voz (SUPER+Y) muda-se em Definições → Voz.
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

Em **Definições → Geral → Atualizações** vês a versão instalada e as novidades, e escolhes: avisar, instalar sozinha, ou
não procurar. Só são oferecidas versões publicadas e assinadas. A atualização verifica a assinatura, faz uma cópia da base
de dados, troca o código e confirma que a PEACE arranca; se não arrancar, repõe a versão anterior e os dados. Os teus dados,
chaves e definições ficam fora da pasta do código. Sincronizar o Moodle é outra coisa e nunca muda o código.

Também podes atualizar à mão em Linux:

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
- As chaves de API não vão para a base de dados nem para as cópias de segurança. Num ficheiro encriptado, a chave de
  encriptação deriva do token da PEACE, que está noutro sítio; quem conseguir ler ambos com a tua conta consegue ler as
  chaves, como acontece com qualquer segredo que um serviço tenha de usar sozinho.
- Mudar o token da PEACE torna esse ficheiro ilegível: basta voltar a escrever as chaves.

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
