# Installs a new version of PEACE on Windows when PEACE asks for it (Settings > Geral). Same order as update.sh: ask,
# download, verify the signature, back up the database, swap the code, start, check health, and go back if it fails.
# Scheduled every 15 minutes by install.ps1. Does nothing unless an update is due.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$App = Split-Path $PSScriptRoot -Parent
$Top = Split-Path $App -Parent
$Url = 'http://127.0.0.1:8420'
$EnvFile = ((Get-Content (Join-Path $App '.env') | Where-Object { $_ -like 'PEACE_ENV_FILE=*' }) -split '=', 2)[1]
$Token = ((Get-Content $EnvFile | Where-Object { $_ -like 'PEACE_API_TOKEN=*' }) -split '=', 2)[1]
$Auth = @{ Authorization = "Bearer $Token" }

function Report($status, $message) {
    try { Invoke-RestMethod -Method Post "$Url/api/v1/updates/report" -Headers $Auth -ContentType 'application/json' -Body (@{ status = $status; message = $message } | ConvertTo-Json) | Out-Null } catch { }
}
function Healthy {
    for ($i = 0; $i -lt 120; $i++) {
        try { Invoke-WebRequest -UseBasicParsing "$Url/health" -TimeoutSec 3 | Out-Null; return $true } catch { Start-Sleep 5 }
    }
    return $false
}
function Compose { Push-Location $App; try { docker compose @args; if ($LASTEXITCODE -ne 0) { throw "docker compose $($args[0])" } } finally { Pop-Location } }

try { $Pending = Invoke-RestMethod "$Url/api/v1/updates/pending" -Headers $Auth -TimeoutSec 20 } catch { exit 0 }  # PEACE is off: next time
if (-not $Pending.install) { exit 0 }
$Mutex = New-Object Threading.Mutex($false, 'PEACE-update')
if (-not $Mutex.WaitOne(0)) { exit 0 }

$Version = $Pending.version
$Work = Join-Path $Top ".peace-update-$PID"
$Backup = Join-Path $Top "before-$Version.dump"
New-Item -ItemType Directory -Force (Join-Path $Work 'new') | Out-Null
try {
    Report 'installing' $Version
    Invoke-WebRequest -UseBasicParsing $Pending.bundle -OutFile (Join-Path $Work 'peace.tar.gz')
    Invoke-WebRequest -UseBasicParsing $Pending.signature -OutFile (Join-Path $Work 'peace.sig')
    # Verified by the running version's own code and key, inside its container.
    Compose run --rm --no-deps -T -v "${Work}:/update" api python -m peace.release unpack /update/peace.tar.gz /update/peace.sig $Version /update/new
    # cmd keeps the dump's bytes: PowerShell's own redirection would re-encode them.
    Push-Location $App
    cmd /c "docker compose exec -T db pg_dump -U peace -Fc peace > `"$Backup`""
    $dumped = $LASTEXITCODE
    Pop-Location
    if ($dumped -ne 0 -or (Get-Item $Backup).Length -eq 0) { throw 'backup' }
} catch {
    Report 'failed' "$_"
    Remove-Item -Recurse -Force $Work -ErrorAction SilentlyContinue
    exit 1
}

$Agent = Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe'" | Where-Object { $_.CommandLine -like '*peace_windows.py*' }
$Agent | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }  # the running agent holds files in the code folder
Copy-Item (Join-Path $App '.env') (Join-Path $Work 'new\.env')
$Previous = "$App.previous"
if (Test-Path $Previous) { Remove-Item -Recurse -Force $Previous }
Set-Location $Top
Move-Item $App $Previous
Move-Item (Join-Path $Work 'new') $App

$Worked = $false
try { Compose up -d --build; $Worked = Healthy } catch { $Worked = $false }
if ($Worked) {
    Report 'done' $Version
} else {
    Remove-Item -Recurse -Force "$App.failed" -ErrorAction SilentlyContinue
    Move-Item $App "$App.failed"
    Move-Item $Previous $App
    Compose up -d db
    Push-Location $App
    cmd /c "docker compose exec -T db pg_restore -U peace -d peace --clean --if-exists < `"$Backup`""
    Pop-Location
    Compose up -d --build
    if (Healthy) { Report 'failed' 'start' }
}
Remove-Item -Recurse -Force $Work -ErrorAction SilentlyContinue
$Startup = Join-Path ([Environment]::GetFolderPath('Startup')) 'PEACE.lnk'
if (Test-Path $Startup) { Start-Process $Startup }  # the agent again, from whichever code is in place
