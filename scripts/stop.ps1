# Stop InfoPoint's services. Bionic is left running (other apps such as AiTrading may use it).
# The worker finishes its current file (up to 60 s) or hands it back to the queue for the next start.
$ErrorActionPreference = "Continue"
Set-Location (Split-Path -Parent $PSScriptRoot)
docker compose -f docker-compose.yml -f docker-compose.roots.yml stop
