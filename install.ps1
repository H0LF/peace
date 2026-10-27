# PEACE on Windows 10/11, in one command (PowerShell):
#   irm https://raw.githubusercontent.com/H0LF/peace/main/install.ps1 | iex
#
# Installs what is missing (WSL 2, Docker Desktop, Ollama, uv), picks the language model this PC can run, starts
# PEACE in Docker and the desktop agent at every sign-in, then opens the dashboard already signed in.
# Windows asks for permission (UAC) for WSL and Docker. If it needs a restart, this script runs again by itself
# after you sign in. Safe to run again: it updates PEACE and keeps your data and settings.

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'  # Invoke-WebRequest is many times slower with the progress bar
$Repo = 'https://github.com/H0LF/peace'
$Self = 'https://raw.githubusercontent.com/H0LF/peace/main/install.ps1'
$PeaceHome = Join-Path $env:LOCALAPPDATA 'PEACE'
$App = Join-Path $PeaceHome 'app'
$Secrets = Join-Path $PeaceHome 'secrets'
$NoBom = New-Object System.Text.UTF8Encoding $false  # Python and Docker read these files; a BOM would break them

function Say($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Fail($text) { throw $text }  # never `exit`: under `irm | iex` it would close the window before it is read
function Have($command) { [bool](Get-Command $command -ErrorAction SilentlyContinue) }
function Update-SessionPath {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
}
function Write-TextFile($path, $lines) { [IO.File]::WriteAllLines($path, [string[]]$lines, $NoBom) }
function New-Secret {
    $bytes = New-Object byte[] 32
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}
function Register-Resume {
    # Run this installer again once, at the next sign-in.
    $command = "powershell -NoProfile -ExecutionPolicy Bypass -Command `"irm $Self | iex`""
    Set-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce' -Name 'PEACE install' -Value $command
}

function Install-Peace {
# --- 0. Windows and winget -------------------------------------------------------------------------------
if ([Environment]::OSVersion.Version.Build -lt 19044) { Fail 'A PEACE precisa do Windows 10 21H2 ou mais recente (ou do Windows 11).' }
if (-not (Have winget)) {
    Fail 'Falta o winget. Instala o «Instalador de Aplicações» (App Installer) a partir da Microsoft Store e corre este comando outra vez.'
}
New-Item -ItemType Directory -Force -Path $PeaceHome, $Secrets | Out-Null

# --- 1. WSL 2 (Docker runs PEACE's server inside it) --------------------------------------------------------
wsl.exe --status *> $null
if ($LASTEXITCODE -ne 0) {
    Say 'A instalar o WSL 2 (o Windows vai pedir autorização)…'
    $wsl = Start-Process wsl.exe -ArgumentList '--install', '--no-distribution' -Verb RunAs -Wait -PassThru
    if ($wsl.ExitCode -ne 0) {  # an older wsl.exe: switch the two Windows features on directly
        Start-Process dism.exe -ArgumentList '/online', '/enable-feature', '/featurename:Microsoft-Windows-Subsystem-Linux',
            '/featurename:VirtualMachinePlatform', '/all', '/norestart' -Verb RunAs -Wait
    }
    Register-Resume
    Write-Host "`nReinicia o PC. Depois de entrares na tua conta, a instalação continua sozinha." -ForegroundColor Yellow
    return
}

# --- 2. Docker Desktop -------------------------------------------------------------------------------------
$DockerExe = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
if (-not (Test-Path $DockerExe)) {
    Say 'A instalar o Docker Desktop (o Windows vai pedir autorização)…'
    winget install -e --id Docker.DockerDesktop --accept-package-agreements --accept-source-agreements --silent
    if (-not (Test-Path $DockerExe)) { Fail 'O Docker Desktop não ficou instalado. Instala-o em https://www.docker.com/products/docker-desktop e corre este comando outra vez.' }
    Register-Resume
    Write-Host "`nO Docker Desktop precisa que termines a sessão do Windows (ou reinicies). A instalação continua sozinha depois." -ForegroundColor Yellow
    return
}
Update-SessionPath
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Say 'A abrir o Docker Desktop… (na primeira vez, aceita os termos na janela do Docker)'
    Start-Process $DockerExe
    $deadline = (Get-Date).AddMinutes(5)
    do { Start-Sleep 5; docker info *> $null } until ($LASTEXITCODE -eq 0 -or (Get-Date) -gt $deadline)
    if ($LASTEXITCODE -ne 0) { Fail 'O Docker não arrancou. Abre o Docker Desktop, aceita os termos, espera que diga «Engine running» e corre este comando outra vez.' }
}

# --- 3. Ollama and the model this PC can run -------------------------------------------------------------------
if (-not (Have ollama)) {
    Say 'A instalar o Ollama (o modelo de linguagem local)…'
    winget install -e --id Ollama.Ollama --accept-package-agreements --accept-source-agreements --silent
    Update-SessionPath
}
if (-not (Have ollama)) { Fail 'O Ollama não ficou instalado. Instala-o em https://ollama.com e corre este comando outra vez.' }
$OllamaUp = $false
for ($i = 0; $i -lt 30 -and -not $OllamaUp; $i++) {
    try { Invoke-WebRequest -UseBasicParsing http://127.0.0.1:11434/api/tags -TimeoutSec 2 | Out-Null; $OllamaUp = $true }
    catch { if ($i -eq 0) { Start-Process ollama -ArgumentList 'serve' -WindowStyle Hidden }; Start-Sleep 2 }
}

# Video memory: the registry holds the real 64-bit size (WMI's AdapterRAM stops at 4 GB).
$Vram = 0
Get-ChildItem 'HKLM:\SYSTEM\ControlSet001\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}' -ErrorAction SilentlyContinue | ForEach-Object {
    $size = (Get-ItemProperty $_.PSPath -Name 'HardwareInformation.qwMemorySize' -ErrorAction SilentlyContinue).'HardwareInformation.qwMemorySize'
    if ($size -and [double]$size -gt $Vram) { $Vram = [double]$size }
}
$VramGB = [math]::Round($Vram / 1GB, 1)
$RamGB = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)
$Threads = (Get-CimInstance Win32_Processor | Measure-Object NumberOfLogicalProcessors -Sum).Sum
# The assistant needs a model that can pick commands (tool calling): the biggest one this PC runs well.
if ($VramGB -ge 14) { $Model = 'gpt-oss:20b'; $Why = "placa gráfica com $VramGB GB" }
elseif ($VramGB -ge 7 -or $RamGB -ge 32) { $Model = 'qwen3:8b'; $Why = "$VramGB GB de placa gráfica, $RamGB GB de RAM" }
elseif ($RamGB -ge 12) { $Model = 'qwen3:4b'; $Why = "$RamGB GB de RAM, sem placa gráfica dedicada suficiente" }
else { $Model = 'llama3.2:3b'; $Why = "só $RamGB GB de RAM" }
$Stt = if ($Threads -ge 12) { 'medium' } else { 'small' }
Say "Modelo do assistente: $Model ($Why). Reconhecimento de voz: Whisper $Stt."
if ($OllamaUp) {
    $installed = @((Invoke-RestMethod http://127.0.0.1:11434/api/tags).models | ForEach-Object { $_.name })
    if ($installed -notcontains $Model) {
        Say "A descarregar $Model (pode demorar; só acontece uma vez)…"
        ollama pull $Model
    }
} else {
    Write-Host 'O Ollama não respondeu: a PEACE funciona sem modelo (frases fixas). Em Definições > Assistente vês quando ficar disponível.' -ForegroundColor Yellow
}

# --- 4. PEACE itself ------------------------------------------------------------------------------------------
Say 'A descarregar a PEACE…'
$zip = Join-Path $env:TEMP 'peace-main.zip'
Invoke-WebRequest -UseBasicParsing "$Repo/archive/refs/heads/main.zip" -OutFile $zip
$unpacked = Join-Path $env:TEMP 'peace-unpacked'
if (Test-Path $unpacked) { Remove-Item -Recurse -Force $unpacked }
Expand-Archive $zip -DestinationPath $unpacked
Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe'" | Where-Object { $_.CommandLine -like '*peace_windows.py*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }  # the running agent holds files in $App
if (Test-Path $App) { Remove-Item -Recurse -Force $App }  # only code: data lives in Docker volumes and $Secrets
Move-Item (Join-Path $unpacked 'peace-main') $App
Remove-Item -Recurse -Force $unpacked, $zip

$EnvFile = Join-Path $Secrets 'peace.env'
$DbPassword = Join-Path $Secrets 'db_password'
if (-not (Test-Path $DbPassword)) { Write-TextFile $DbPassword @(New-Secret) }
if (-not (Test-Path $EnvFile) -or -not (Select-String -Path $EnvFile -Pattern '^PEACE_API_TOKEN=' -Quiet)) {
    Write-TextFile $EnvFile @("PEACE_API_TOKEN=$(New-Secret)")
}
$Token = ((Get-Content $EnvFile | Where-Object { $_ -like 'PEACE_API_TOKEN=*' }) -split '=', 2)[1]
Write-TextFile (Join-Path $App '.env') @("PEACE_ENV_FILE=$EnvFile", "PEACE_DB_PASSWORD_FILE=$DbPassword", "PEACE_STT_MODEL=$Stt")

Say 'A preparar os serviços da PEACE no Docker (na primeira vez demora alguns minutos)…'
Push-Location $App
try {
    docker compose up -d --build
    if ($LASTEXITCODE -ne 0) { Fail 'O Docker não conseguiu arrancar a PEACE. Vê as mensagens acima.' }
} finally { Pop-Location }
$Up = $false
$deadline = (Get-Date).AddMinutes(3)
while (-not $Up -and (Get-Date) -lt $deadline) {
    Start-Sleep 3
    try { Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8420/health -TimeoutSec 3 | Out-Null; $Up = $true } catch { $Up = $false }
}
if (-not $Up) { Fail "A PEACE não respondeu em http://127.0.0.1:8420. Vê o estado com: docker compose -f `"$App\compose.yaml`" logs" }

# --- 5. Desktop agent: notifications, push-to-talk, PC actions ----------------------------------------------------
$Uv = Join-Path $env:USERPROFILE '.local\bin\uv.exe'
if (Have uv) { $Uv = (Get-Command uv).Source }
elseif (-not (Test-Path $Uv)) {
    Say 'A instalar o uv (gere o Python do agente)…'
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
}
$Venv = Join-Path $PeaceHome 'agent'
if (-not (Test-Path (Join-Path $Venv 'Scripts\pythonw.exe'))) { & $Uv venv $Venv --python 3.13 }
& $Uv pip install --python (Join-Path $Venv 'Scripts\python.exe') --quiet sounddevice numpy windows-toasts
if ($LASTEXITCODE -ne 0) { Fail 'Não foi possível instalar o agente do PC (Python).' }
$DesktopEnv = Join-Path $PeaceHome 'desktop.env'
if (-not (Test-Path $DesktopEnv)) {
    Write-TextFile $DesktopEnv @('PEACE_URL=http://127.0.0.1:8420', "PEACE_TOKEN=$Token", 'PEACE_PTT=SUPER + Y',
                               'PEACE_TTS=supertonic3', 'PEACE_TTS_VOICE=F1')
}
$Startup = Join-Path ([Environment]::GetFolderPath('Startup')) 'PEACE.lnk'
$link = (New-Object -ComObject WScript.Shell).CreateShortcut($Startup)
$link.TargetPath = Join-Path $Venv 'Scripts\pythonw.exe'
$link.Arguments = '"' + (Join-Path $App 'desktop\peace_windows.py') + '"'
$link.WorkingDirectory = Join-Path $App 'desktop'
$link.Description = 'PEACE: notificações e voz'
$link.Save()
Start-Process $link.TargetPath -ArgumentList $link.Arguments -WorkingDirectory $link.WorkingDirectory

# --- Done -------------------------------------------------------------------------------------------------
Start-Process "http://127.0.0.1:8420/entrar#token=$Token"
Write-Host "`nA PEACE está instalada e aberta no browser." -ForegroundColor Green
Write-Host 'Em Definições > Integrações escreve o endereço da plataforma da tua universidade (Moodle e/ou Nónio),'
Write-Host 'o utilizador e a palavra-passe, e carrega em «Sincronizar».'
Write-Host "Token de acesso (para outro browser): $Token"
}

Install-Peace
