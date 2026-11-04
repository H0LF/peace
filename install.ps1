# PEACE on Windows 10/11, in one command (PowerShell):
#   irm https://raw.githubusercontent.com/H0LF/peace/main/install.ps1 | iex
#
# Looks at this PC and lets you choose the AI model (with a recommendation), installs what is missing (WSL 2, Docker
# Desktop, Ollama, uv), starts PEACE in Docker and the desktop agent at every sign-in, then opens the dashboard signed in.
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

# What the assistant can run, best first: only models that can pick commands (tool calling), like llm.PREFERRED.
function Get-ModelChoice($VramGB, $RamGB, $FreeGB) {
    $spare = $FreeGB - 15  # shortcut: ~15 GB for Docker, PEACE and the voice models, measure on a clean PC if it is tight
    @(
        @{ Model = 'gpt-oss:20b'; SizeGB = 13; Label = 'o melhor; precisa de placa gráfica com 14 GB'; Fits = ($VramGB -ge 14) }
        @{ Model = 'qwen3:8b'; SizeGB = 5.2; Label = 'muito bom; 7 GB de placa gráfica ou 32 GB de RAM'; Fits = ($VramGB -ge 7 -or $RamGB -ge 32) }
        @{ Model = 'qwen3:4b'; SizeGB = 2.6; Label = 'equilibrado; 12 GB de RAM'; Fits = ($RamGB -ge 12) }
        @{ Model = 'llama3.2:3b'; SizeGB = 2; Label = 'leve, para portáteis modestos; 6 GB de RAM'; Fits = ($RamGB -ge 6) }
        @{ Model = ''; SizeGB = 0; Label = 'sem modelo: a PEACE responde com frases fixas'; Fits = $true }
    ) | ForEach-Object { $_.Room = ($_.SizeGB -le $spare -or -not $_.Model); [pscustomobject]$_ }
}
function Get-RecommendedChoice($choices) { @($choices | Where-Object { $_.Fits -and $_.Room })[0] }
# One key picks an option; Enter or the time limit takes the recommendation, so the install never waits for an answer.
function Read-Choice($allowed, $default, $seconds) {
    try {
        $deadline = (Get-Date).AddSeconds($seconds)
        while ((Get-Date) -lt $deadline) {
            if ([Console]::KeyAvailable) {
                $key = [Console]::ReadKey($true)
                if ($key.Key -eq 'Enter') { return $default }
                if ($allowed -contains "$($key.KeyChar)") { return [int]"$($key.KeyChar)" }
            }
            [Threading.Thread]::Sleep(100)
        }
    } catch { return $default }  # no console to read from
    $default
}

function Install-Peace {
# --- 0. Windows and winget -------------------------------------------------------------------------------
if ([Environment]::OSVersion.Version.Build -lt 19044) { Fail 'A PEACE precisa do Windows 10 21H2 ou mais recente (ou do Windows 11).' }
if (-not (Have winget)) {
    Fail 'Falta o winget. Instala o «Instalador de Aplicações» (App Installer) a partir da Microsoft Store e corre este comando outra vez.'
}
New-Item -ItemType Directory -Force -Path $PeaceHome, $Secrets | Out-Null
[Console]::OutputEncoding = $NoBom

# --- 0. This PC, and which AI model to use (asked once, before the long part, and kept across restarts) -----------
# Video memory: the registry holds the real 64-bit size (WMI's AdapterRAM stops at 4 GB).
$Vram = 0
Get-ChildItem 'HKLM:\SYSTEM\ControlSet001\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}' -ErrorAction SilentlyContinue | ForEach-Object {
    $size = (Get-ItemProperty $_.PSPath -Name 'HardwareInformation.qwMemorySize' -ErrorAction SilentlyContinue).'HardwareInformation.qwMemorySize'
    if ($size -and [double]$size -gt $Vram) { $Vram = [double]$size }
}
$VramGB = [math]::Round($Vram / 1GB, 1)
$RamGB = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)
$FreeGB = [math]::Round((Get-PSDrive $env:SystemDrive.TrimEnd(':')).Free / 1GB, 1)
$Stt = 'small'  # "medium" hears little better and answers twice as slowly

$ModelFile = Join-Path $PeaceHome 'model.txt'
if (Test-Path $ModelFile) {
    $Model = (Get-Content $ModelFile -Raw).Trim()
    if ($Model -eq 'none') { $Model = '' }
} else {
    $choices = @(Get-ModelChoice $VramGB $RamGB $FreeGB)
    $best = Get-RecommendedChoice $choices
    Say "Este PC: $VramGB GB de placa gráfica, $RamGB GB de RAM, $FreeGB GB livres em disco."
    Write-Host 'Que modelo de IA queres para o assistente? Fica tudo neste PC.'
    $allowed = @()
    for ($i = 0; $i -lt $choices.Count; $i++) {
        $c = $choices[$i]
        $note = if (-not $c.Room) { ' [sem espaço em disco]' } elseif ($c -eq $best) { ' [RECOMENDADO]' } elseif (-not $c.Fits) { ' [ficaria lento neste PC]' } else { '' }
        if ($c.Room) { $allowed += "$($i + 1)" }
        $name = if ($c.Model) { "$($c.Model), $($c.SizeGB) GB" } else { 'Nenhum' }
        Write-Host "  $($i + 1). $name - $($c.Label)$note"
    }
    $default = [array]::IndexOf($choices, $best) + 1
    Write-Host "Carrega no número. Enter ou 30 segundos: opção $default. Podes juntar outro modelo mais tarde."
    Write-Host 'Ao continuar, aceitas também os termos do Docker Desktop: https://www.docker.com/legal/docker-subscription-service-agreement'
    $Model = $choices[(Read-Choice $allowed $default 30) - 1].Model
    Write-TextFile $ModelFile @($(if ($Model) { $Model } else { 'none' }))
}
Say "Modelo do assistente: $(if ($Model) { $Model } else { 'nenhum' }). Reconhecimento de voz: Whisper $Stt."

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
    winget install -e --id Docker.DockerDesktop --accept-package-agreements --accept-source-agreements --override 'install --quiet --accept-license --backend=wsl-2'
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

# --- 3. Ollama and the chosen model (nothing at all if the user chose none) ---------------------------------------
if ($Model) {
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
    if ($OllamaUp) {
        $installed = @((Invoke-RestMethod http://127.0.0.1:11434/api/tags).models | ForEach-Object { $_.name })
        if ($installed -notcontains $Model) {
            Say "A descarregar $Model (pode demorar; só acontece uma vez)…"
            ollama pull $Model
        }
    } else {
        Write-Host 'O Ollama não respondeu: a PEACE funciona sem modelo (frases fixas). Em Definições > Assistente vês quando ficar disponível.' -ForegroundColor Yellow
    }
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
# The model the user chose, even if a bigger one was already on this PC. Settings > Assistente can still change it.
$kept = @(Get-Content $EnvFile | Where-Object { $_ -notlike 'PEACE_LLM_MODEL=*' })
$kept = @($kept | Where-Object { $_ -notlike 'PEACE_INSTALL_KIND=*' })
Write-TextFile $EnvFile ($kept + @('PEACE_INSTALL_KIND=docker') + @($(if ($Model) { "PEACE_LLM_MODEL=$Model" })))
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
                               'PEACE_TTS=piper', 'PEACE_TTS_VOICE=tugao')
}
$Startup = Join-Path ([Environment]::GetFolderPath('Startup')) 'PEACE.lnk'
$link = (New-Object -ComObject WScript.Shell).CreateShortcut($Startup)
$link.TargetPath = Join-Path $Venv 'Scripts\pythonw.exe'
$link.Arguments = '"' + (Join-Path $App 'desktop\peace_windows.py') + '"'
$link.WorkingDirectory = Join-Path $App 'desktop'
$link.Description = 'PEACE: notificações e voz'
$link.Save()
Start-Process $link.TargetPath -ArgumentList $link.Arguments -WorkingDirectory $link.WorkingDirectory

# --- 6. Updates: Settings > Geral decides; this task asks PEACE every 15 minutes whether one is due ----------------
$UpdateAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$App\scripts\update.ps1`""
$UpdateTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 15)
Register-ScheduledTask -TaskName 'PEACE update' -Action $UpdateAction -Trigger $UpdateTrigger -Force | Out-Null

# --- Done -------------------------------------------------------------------------------------------------
Start-Process "http://127.0.0.1:8420/entrar#token=$Token"
Write-Host "`nA PEACE está instalada e aberta no browser." -ForegroundColor Green
Write-Host "Para a usar também noutro portátil ou no telemóvel: powershell -ExecutionPolicy Bypass -File `"$App\scripts\remote-access.ps1`""
Write-Host 'Em Definições > Integrações escreve o endereço da plataforma da tua universidade (Moodle e/ou Nónio),'
Write-Host 'o utilizador e a palavra-passe, e carrega em «Sincronizar».'
Write-Host "Token de acesso (para outro browser): $Token"
}

Install-Peace
