# Use PEACE from your other devices (laptop, phone), with everything staying on this Windows PC.
# Tailscale gives this PC a private https address only your own devices can reach: no port is opened to the
# internet and PEACE keeps listening on 127.0.0.1. Https is also what lets browsers use the microphone.
# Safe to run again. To stop: tailscale serve --https=443 off
$ErrorActionPreference = 'Stop'

if (-not (Get-Command tailscale -ErrorAction SilentlyContinue)) {
    winget install -e --id Tailscale.Tailscale --accept-package-agreements --accept-source-agreements --silent
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
}
if (((tailscale status --json 2>$null) | ConvertFrom-Json).BackendState -ne 'Running') {
    Write-Host 'A ligar este PC ao Tailscale: entra na tua conta na janela que abrir.'
    tailscale up
}
tailscale serve --bg 8420  # may print a link to enable Serve, then waits for it
if ($LASTEXITCODE -ne 0) { throw 'O Tailscale não conseguiu publicar a PEACE. Vê as mensagens acima.' }

$PeaceAddress = ((tailscale status --json) | ConvertFrom-Json).Self.DNSName.TrimEnd('.')
$EnvFile = Join-Path $env:LOCALAPPDATA 'PEACE\secrets\peace.env'
$Token = ((Get-Content $EnvFile | Where-Object { $_ -like 'PEACE_API_TOKEN=*' }) -split '=', 2)[1]

Write-Host "`nA PEACE está disponível nos teus dispositivos em:`n  https://$PeaceAddress"
Write-Host "`nEm cada dispositivo: instala o Tailscale (https://tailscale.com/download), entra na MESMA conta e abre este link"
Write-Host "uma vez para iniciar sessão. O link contém a tua chave de acesso: não o partilhes.`n  https://$PeaceAddress/entrar#token=$Token"
Write-Host "`nEste PC tem de estar ligado e acordado. Se o Tailscale pedir para ativar HTTPS, aceita o link que ele mostrar."
