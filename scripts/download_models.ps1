# Download the embedding model (bge-m3) into data\models using the api image. Needs internet once.
$ErrorActionPreference = "Continue"
$Repo = Split-Path -Parent $PSScriptRoot
New-Item -ItemType Directory -Force "$Repo\data\models" | Out-Null
docker run --rm -e HF_HUB_OFFLINE=0 -v "${Repo}\data\models:/models" infopoint-api:latest python -m app.tools.download_models @args
if ($LASTEXITCODE -ne 0) { throw "Model download failed." }
