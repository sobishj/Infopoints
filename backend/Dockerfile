# Build context: repository root.

# ---------------------------------------------------------------- frontend build
FROM node:22-bookworm-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------- shared python base
FROM python:3.11-slim-bookworm AS base
ENV PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    DO_NOT_TRACK=1
WORKDIR /app
COPY backend/requirements-common.txt ./
RUN pip install -r requirements-common.txt

# ---------------------------------------------------------------- api
FROM base AS api
COPY backend/ ./
COPY --from=web /web/dist /app/static
EXPOSE 8080

# ---------------------------------------------------------------- worker (OCR, office conversion, media)
FROM base AS worker
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      tesseract-ocr tesseract-ocr-eng \
      libreoffice-writer-nogui libreoffice-impress-nogui libreoffice-calc-nogui \
      ffmpeg fonts-dejavu fonts-liberation fonts-crosextra-carlito fonts-crosextra-caladea \
 && rm -rf /var/lib/apt/lists/*
COPY backend/requirements-worker.txt ./
RUN pip install -r requirements-worker.txt
COPY backend/ ./
