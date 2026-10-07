<#
  Start InfoPoint (Phase 1 starter; the desktop launcher replaces this in Phase 4).

  1. Creates .env on first run (random secrets, Downloads as the default folder).
  2. Starts Bionic if it isn't serving yet (it's left running afterwards; other apps share it).
  3. Generates the document-root mounts and starts postgres, api and worker in Docker.
  4. Opens InfoPoint in the browser.

  -Build   rebuild the Docker images (after code changes)
#>
param([switch]$Build, [switch]$NoBrowser)
$ErrorActionPreference = "Continue"  # native tools write warnings to stderr; exit codes are checked explicitly
$Repo = Split-Path -Parent $PSScriptRoot
Set-Location $Repo

function Step($m) { Write-Host "`n== $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "   $m" -ForegroundColor Green }
function Warn($m) { Write-Host "   $m" -ForegroundColor Yellow }

function Read-DotEnv($path) {
    $h = @{}
    foreach ($line in Get-Content $path) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') { $h[$Matches[1]] = $Matches[2].Trim().Trim('"') }
    }
    return $h
}

function Set-DotEnvValue($path, $key, $value) {
    $lines = Get-Content $path
    $lines = $lines | ForEach-Object { if ($_ -match "^$key=") { "$key=$value" } else { $_ } }
    Set-Content -Path $path -Value $lines -Encoding utf8
}

function New-Secret { -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 40 | ForEach-Object { [char]$_ }) }

# ---------------------------------------------------------------- 1. configuration
Step "Configuration"
$downloads = (New-Object -ComObject Shell.Application).NameSpace('shell:Downloads').Self.Path
if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Set-DotEnvValue .env "APP_SECRET_KEY" (New-Secret)
    Set-DotEnvValue .env "INTERNAL_TOKEN" (New-Secret)
    $pw = New-Secret
    Set-DotEnvValue .env "INITIAL_ADMIN_PASSWORD" $pw
    Ok "Created .env (admin password: $pw - also stored in .env)"
}
$envs = Read-DotEnv .env
if (-not $envs["DEFAULT_DOC_FOLDER"]) {
    Set-DotEnvValue .env "DEFAULT_DOC_FOLDER" $downloads
    $envs["DEFAULT_DOC_FOLDER"] = $downloads
}
Ok "Default document folder: $($envs['DEFAULT_DOC_FOLDER'])"
python scripts/gen_roots.py
if ($LASTEXITCODE -ne 0) { throw "Could not generate docker-compose.roots.yml" }

# ---------------------------------------------------------------- 2. Docker
Step "Docker"
docker info --format "{{.ServerVersion}}" 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    Write-Host "   Waiting for Docker Desktop..."
    for ($i = 0; $i -lt 60; $i++) { Start-Sleep 3; docker info --format "{{.ServerVersion}}" 2>$null | Out-Null; if ($LASTEXITCODE -eq 0) { break } }
    if ($LASTEXITCODE -ne 0) { throw "Docker Desktop did not start." }
}
Ok "Docker is running."

# ---------------------------------------------------------------- 3. Bionic (local Qwen)
Step "Bionic (local AI model)"
$health = $envs["BIONIC_HEALTH_URL"]
$bionicUp = { try { Invoke-RestMethod -Uri $health -TimeoutSec 3 | Out-Null; $true } catch { $false } }
if (& $bionicUp) {
    Ok "Bionic was already running."
} elseif ($envs["MANAGE_BIONIC"] -eq "true") {
    $exe = [Environment]::ExpandEnvironmentVariables($envs["BIONIC_START_CMD"])
    Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue  # set in VS Code terminals; breaks Electron apps
    Start-Process $exe
    $timeout = [int]($envs["BIONIC_START_TIMEOUT"] | ForEach-Object { if ($_) { $_ } else { 180 } })
    $deadline = (Get-Date).AddSeconds($timeout)
    while (-not (& $bionicUp) -and (Get-Date) -lt $deadline) { Start-Sleep 2 }
    if (& $bionicUp) { Ok "Bionic started." } else { Warn "Bionic did not answer within $timeout s. Answers will fail until it does." }
} else {
    Warn "Bionic is not running (MANAGE_BIONIC=false, so it isn't started)."
}

# ---------------------------------------------------------------- 4. services
Step "InfoPoint services"
$compose = @("compose", "-f", "docker-compose.yml", "-f", "docker-compose.roots.yml")
$images = docker images -q infopoint-api:latest
if ($Build -or -not $images) {
    & docker @compose build
    if ($LASTEXITCODE -ne 0) { throw "Docker build failed." }
}
# Installs the configured embedding model if missing (needs internet once; a no-op afterwards).
& "$PSScriptRoot\download_models.ps1"
& docker @compose up -d
if ($LASTEXITCODE -ne 0) { throw "docker compose up failed." }

$port = if ($envs["APP_PORT"]) { $envs["APP_PORT"] } else { "8765" }
$url = "http://localhost:$port"
$ready = $false
for ($i = 0; $i -lt 90; $i++) {
    try { Invoke-RestMethod "$url/api/health" -TimeoutSec 3 | Out-Null; $ready = $true; break } catch { Start-Sleep 2 }
}
if (-not $ready) { throw "InfoPoint didn't become ready. Check: docker compose logs api" }
Ok "InfoPoint is running at $url"
if (-not $NoBrowser) { Start-Process $url }
