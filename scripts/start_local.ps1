# Windows rootless Podman start. Preserve named volumes and existing configuration.
[CmdletBinding()]
param([switch]$Recreate)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    $env:PODMAN_COMPOSE_PROVIDER = (Get-Command podman-compose -ErrorAction Stop).Source
    $rootless = & podman info --format '{{.Host.Security.Rootless}}'
    if ($LASTEXITCODE -ne 0 -or ($rootless | Out-String).Trim() -ne 'true') {
        throw 'A running rootless Podman Machine/connection is required. See docs/podman.md.'
    }
    # podman-compose 1.6.0 mistakes C: contexts for Git URLs and drops -f.
    # Build explicitly, then use the same Compose services/images without builds.
    & podman build -f ./backend/Containerfile -t localhost/ingest-workbench-api:latest .
    if ($LASTEXITCODE -ne 0) { throw 'Backend image build failed.' }
    & podman build -f ./frontend/Containerfile -t localhost/ingest-workbench:latest ./frontend
    if ($LASTEXITCODE -ne 0) { throw 'Workbench image build failed.' }
    $composeArgs = @('compose')
    $composeArgs += @('up', '--no-build', '-d')
    if ($Recreate) { $composeArgs += '--force-recreate' }
    & podman @composeArgs
    if ($LASTEXITCODE -ne 0) { throw 'Compose startup failed; inspect podman compose logs.' }
    & podman compose ps
    if ($LASTEXITCODE -ne 0) { throw 'Compose status check failed.' }
    # Compose binds IPv4 loopback. Avoid localhost IPv6 fallback delays on Windows.
    $probeUrls = @('http://127.0.0.1:8000/ready', 'http://127.0.0.1:8080/realms/ingest-workbench/.well-known/openid-configuration', 'http://127.0.0.1:4200/')
    $deadline = [DateTime]::UtcNow.AddMinutes(3)
    do {
        $pending = @()
        foreach ($probeUrl in $probeUrls) {
            try { $null = Invoke-WebRequest -Uri $probeUrl -TimeoutSec 2 -UseBasicParsing }
            catch { $pending += $probeUrl }
        }
        if ($pending.Count -eq 0) { break }
        Write-Output ('Waiting for local readiness: ' + ($pending -join ', '))
        Start-Sleep -Seconds 2
    } while ([DateTime]::UtcNow -lt $deadline)
    if ($pending.Count -gt 0) { throw ('Local readiness deadline exceeded: ' + ($pending -join ', ')) }
    & podman compose exec -T kost-worker python -m app.kost_worker --check
    if ($LASTEXITCODE -ne 0) { throw 'KOST background worker is not ready; inspect podman compose logs kost-worker.' }
    Write-Output 'Local stack ready. Archival acceptance remains disabled; browser and live integration qualification are separate.'
} finally {
    Pop-Location
}
