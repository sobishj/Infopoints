# InfoPoint — Implementation Plan

Offline, self-hosted RAG assistant that answers questions only from project documents, audio and video,
with page- and timestamp-level citations. This document is the design baseline for Phases 1–4.

Status: **draft, awaiting approval**.

---

## 0. Key decisions (and where they deviate from / sharpen the brief)

| # | Decision | Why |
|---|----------|-----|
| D1 | **Host-path ↔ container-path mapping.** `ALLOWED_DOC_ROOTS` is written in *host* terms (`D:\`, `\\fileserver\projects`, `/mnt/shares`). Each root gets a stable alias and is bind-mounted read-only at `/roots/<alias>`. The DB stores the host path (what users see/paste) and translates to container paths internally. | The native "Browse…" dialog returns host paths; backend/worker run in Docker and see different paths. |
| D2 | **Polling watcher on mounted folders** (`watchdog` `PollingObserver`, 20 s) + full hash rescan every `RESCAN_INTERVAL` (default 5 min). Native observer only when the backend runs on the same OS as the files. | Docker Desktop on Windows does not forward file-change events through bind mounts, and SMB shares miss events. Needed for the "answerable within 2 minutes" criterion. |
| D3 | **One embedding model process.** The backend loads bge-m3 once and exposes an internal-only `/internal/embed` endpoint; the worker calls it in batches. | bge-m3 is about 2.3 GB in RAM. Docker Desktop gives WSL2 about 8 GB by default on this 16 GB machine, which also has to hold Qwen, Whisper, LibreOffice and Postgres. Loading the model twice wouldn't fit comfortably. (Alternative: a separate `embedder` container. Same idea, one more service.) |
| D4 | **Two worker lanes:** `doc` lane (N parallel slots, default 2) and `media` lane (1 slot). Jobs are claimed with `SELECT … FOR UPDATE SKIP LOCKED` filtered by lane. | A 1-hour video transcription can never block PDFs. |
| D5 | **Browser-playable media proxies.** mp4/m4a/mp3/wav with browser-safe codecs are streamed as-is. mkv/avi/mov or unsupported codecs get an ffmpeg remux (fast) or, if needed, a low-res H.264/AAC transcode cached under `/data/media_cache`. | Browsers can't play most mkv/avi files, so "Play from 52:10" would otherwise fail. |
| D6 | **Context budgeting per model.** Chunks are packed into the prompt up to `model.context_length − answer_reserve`. Fewer than 6–8 chunks are sent if they don't fit. | Qwen 1.5B is often served with a 2k–4k context window. 8 × 500-token chunks would be silently truncated. |
| D7 | **Retrieval gate before the LLM.** If the top fused score / best vector similarity is below a threshold, the app returns the "I couldn't find this in the project documents" response (plus "related sources") without asking the LLM. Post-generation citation validation is also enforced. | A 1.5B model tends to answer anyway. Gating makes the "no guess" acceptance criterion deterministic. |
| D8 | **Server-side sessions in Postgres** (HTTP-only cookie), not JWT. Auth goes through an `AuthProvider` interface (`LocalAuthProvider` now, `LdapAuthProvider` later). | Simple revocation, no extra infrastructure, ready for LDAP. |
| D9 | **Postgres 16 + pgvector in InfoPoint's own container**, internal network only. Optional host port 5434 for debugging. | Keeps InfoPoint separate from `aitrading-pg` (which uses port 5433). |
| D10 | **Launcher is packaged as `InfoPoint.exe` (PyInstaller, Python 3.12).** | The host has Python 3.14, and pywebview's WebView2 backend (pythonnet) may not support it yet. A frozen exe also needs no Python on the target machine. |
| D11 | **Live updates via Server-Sent Events** backed by Postgres `LISTEN/NOTIFY` (indexing progress, job state). Answers stream over SSE too. | No Redis/websocket broker needed. |

---

## 1. Architecture

```
                ┌───────────────────────── Desktop launcher (InfoPoint.exe, pywebview) ─────────────────────────┐
                │ splash → Bionic health/start → compose up (pg, api, worker) → main window → close dialog      │
                └──────────────┬──────────────────────────────────────────────────────────────┬─────────────────┘
                               │ http://127.0.0.1:8080                                         │ docker compose
                               ▼                                                               ▼
 Browser / WebView ──► ┌──────────────── api (FastAPI) ────────────────┐            Bionic (separate compose)
  React SPA (bundled)  │ /api/*  auth, ask (SSE), folders, library,    │──OpenAI──► Qwen 1.5B  [QWEN_BASE_URL]
                       │        models, users, health, files (range)   │  compat     (shared with AiTrading)
                       │ /internal/embed  (bge-m3, internal net only)  │
                       │ static: SPA, PDF.js, fonts                    │
                       └──────┬───────────────────────────▲────────────┘
                              │ SQLAlchemy                │ /internal/embed
                              ▼                           │
                       ┌──────────────┐          ┌────────┴──────────────────────────────┐
                       │ postgres 16  │◄─────────│ worker                                 │
                       │ + pgvector   │ jobs,    │  watcher (polling) + rescan scheduler  │
                       │ (all state)  │ NOTIFY   │  doc lane ×2: PyMuPDF, Tesseract, LO   │
                       └──────────────┘          │  media lane ×1: ffmpeg, faster-whisper │
                                                 └────────┬───────────────────────────────┘
                                                          │ read-only bind mounts
                                                          ▼
                                       /roots/<alias>  ←  D:\  \\fileserver\projects  /mnt/shares
```

Runtime modes (`APP_MODE=desktop|server`):
- **desktop**: launcher manages Bionic (`MANAGE_BIONIC=true`), optional single-user auto-login, native folder dialog.
- **server**: `docker compose up -d`, `restart: unless-stopped`, Bionic is never touched, server-side folder browser.

---

## 2. Database schema (PostgreSQL 16 + pgvector, Alembic-managed)

```sql
-- identity & access
users(id, username UNIQUE, display_name, password_hash NULL, auth_source TEXT DEFAULT 'local',
      role TEXT CHECK (role IN ('admin','user')), is_active, created_at, last_login_at)
sessions(id TEXT PK /*random 256-bit*/, user_id FK, created_at, expires_at, ip, user_agent)
user_projects(user_id FK, project_id FK, PRIMARY KEY(user_id, project_id))

-- folders / projects
projects(id, name, parent_id NULL FK projects /*sub-projects*/, folder_id FK, rel_path NULL, created_at)
folders(id, display_name, host_path, root_alias, container_path, enabled, include_subfolders DEFAULT true,
        subfolders_as_subprojects DEFAULT false, file_type_filter TEXT[] NULL,
        last_scan_at, last_scan_status, created_at, updated_at)
        -- each folder owns one top-level project (projects.folder_id)

-- files & content
files(id, folder_id FK, project_id FK, rel_path, file_name, ext, kind /*pdf|office|sheet|text|media*/,
      sha256, size_bytes, mtime, status /*queued|processing|indexed|failed|deleted*/,
      error, page_count NULL, duration_s NULL, derived_pdf_path NULL, media_proxy_path NULL,
      embedding_model, indexed_at, updated_at,
      UNIQUE(folder_id, rel_path))
chunks(id BIGSERIAL, file_id FK ON DELETE CASCADE, project_id FK, ordinal,
       loc_type /*page|slide|sheet_rows|lines|heading|time*/,
       page INT NULL, sheet TEXT NULL, row_start INT NULL, row_end INT NULL,
       line_start INT NULL, line_end INT NULL, heading TEXT NULL,
       t_start REAL NULL, t_end REAL NULL,
       source_header TEXT,           -- "[Source: X.pdf | Page 14]"
       text TEXT, token_count INT,
       tsv tsvector GENERATED ALWAYS AS (to_tsvector(:fts_config, text)) STORED,
       embedding vector(1024))
  INDEX chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops)
  INDEX chunks_tsv_gin        ON chunks USING gin (tsv)
  INDEX chunks_project        ON chunks (project_id)
transcript_segments(id, file_id FK CASCADE, idx, t_start, t_end, text)   -- for the media viewer
page_boxes(file_id FK CASCADE, page, text_spans JSONB)                   -- optional OCR boxes for highlight

-- jobs (queue)
jobs(id BIGSERIAL, lane /*doc|media|system*/, type /*index_file|delete_file|scan_folder|reindex_all|purge_folder*/,
     payload JSONB, status /*queued|running|done|failed|cancelled*/, priority INT,
     attempts INT, max_attempts INT DEFAULT 3, progress REAL, progress_msg TEXT,
     locked_by TEXT, heartbeat_at, run_after, created_at, started_at, finished_at, error)
  INDEX ON jobs (lane, status, priority DESC, id) WHERE status='queued'
  -- claim: UPDATE jobs SET status='running', locked_by=$w ... WHERE id = (SELECT id FROM jobs WHERE lane=$l
  --        AND status='queued' AND run_after<=now() ORDER BY priority DESC, id FOR UPDATE SKIP LOCKED LIMIT 1)
  -- stale heartbeat (>2 min) → re-queued (covers crash and "resume on next start")

-- models
models(id, display_name, provider_type DEFAULT 'openai_compatible', base_url, model_name,
       api_key_enc BYTEA NULL /*Fernet, key from APP_SECRET_KEY*/, context_length INT, temperature REAL,
       max_output_tokens INT, is_default BOOL, enabled BOOL, created_at, updated_at)
  UNIQUE INDEX one_default ON models((true)) WHERE is_default

-- conversations
conversations(id, user_id FK, title, model_id FK, project_ids INT[], created_at, updated_at)
messages(id, conversation_id FK, role /*user|assistant*/, content, status /*ok|not_found|error*/,
         model_id, latency_ms, created_at)
message_citations(message_id FK, marker INT, chunk_id BIGINT NULL /*nullable: chunk may be re-indexed*/,
                  file_id, file_name, loc_label, snippet)

-- settings, audit, ops
settings(key PK, value JSONB, updated_by, updated_at)   -- whisper_size, rescan_interval, bionic options, embedding_model…
audit_log(id, user_id, action, entity, entity_id, before JSONB, after JSONB, created_at)
service_heartbeats(service PK, instance, last_seen, info JSONB)   -- worker health
bionic_events(id, action /*start|stop|health*/, result, detail, created_at)
```

Stale citations: when a file is re-indexed or deleted, its chunks go away (CASCADE). Old messages keep a
frozen snapshot in `message_citations`. If the cited file has changed since, its link shows "source changed since this answer".

---

## 3. Ingestion pipeline

**Discovery**
1. Folder saved → `scan_folder` job (priority high) + watcher registered on the fly via a `folders_changed` NOTIFY (no restart).
2. Scan walks the folder (respecting subfolder/type filters) and diffs against `files` by `(rel_path, size, mtime)`.
   It hashes only candidates that changed (SHA-256, streamed).
3. **Stability gate**: a new or changed file is checked again until its size and mtime have been unchanged for 10 s,
   then an `index_file` job is queued (lane by kind).
4. Hash unchanged → only mtime is updated. Hash changed → delete the old chunks and re-index in **one transaction**,
   so search never shows a half-indexed file. Missing → `status=deleted`, chunks deleted.

**Extractors** (each returns `Segment(text, location)`; chunker never merges across location units)

| Type | Method | Location |
|---|---|---|
| PDF | PyMuPDF per page. If text chars < 50 or the page is mostly image → Tesseract OCR of a 300-dpi render. Embedded images ≥ 200×80 px are OCR'd and appended ("[Screenshot text] …") | page |
| DOCX/DOC/ODT | `soffice --headless --convert-to pdf` → cached derived PDF → PDF path. Original kept for download | page |
| PPTX/PPT/ODP | same conversion | slide (= page) |
| XLSX/XLS/CSV | openpyxl / csv; per sheet, row windows sized to ~500 tokens, header row repeated in each chunk | sheet + rows |
| TXT/MD | MD split by headings, TXT by line windows | heading / line range |
| Audio/Video | ffmpeg → 16 kHz mono WAV → faster-whisper (`int8` on CPU, size from settings, VAD on). Segments stored, grouped into 60–120 s chunks at segment boundaries | t_start–t_end |

**Chunking**: ~500 tokens, ~60 overlap, measured with the embedding model's tokenizer. Never crosses a page/slide/sheet.
**Embedding**: `passage` text = `source_header + "\n" + text`. Batched (32) through `/internal/embed`.
**Progress**: jobs.progress updated per page / per transcribed minute, pushed via NOTIFY → SSE to the Library page.
**Embedding model change**: setting change → banner "Index uses bge-m3; new model X requires re-index" → `reindex_all` job.
If the embedding dimension changes, a migration helper recreates the `vector(n)` column + HNSW index.

Expected CPU throughput on this laptop (to be measured in Phase 1/2): text PDF ≈ 1–3 s/page incl. embedding;
OCR page ≈ 3–6 s; Whisper `small` int8 ≈ 0.3–0.5× real time → a 1-hour video ≈ 20–35 min.

---

## 4. Retrieval & answering

1. Resolve allowed projects = user's `user_projects` ∩ selected projects ∩ enabled folders (admins: all enabled).
   **Enforced in SQL**, never in the client.
2. Query embedding (bge-m3) → top 40 by cosine (HNSW, `hnsw.ef_search=80`) **and** top 40 by `ts_rank_cd` on
   `websearch_to_tsquery` (both filtered by `project_id = ANY(:allowed)`).
3. Reciprocal Rank Fusion (k=60) → dedupe overlapping neighbours → top 8 → **retrieval gate (D7)**.
4. Prompt pack within the model's context budget (D6); sources numbered `[1]..[n]` with their headers.
5. System prompt (versioned in code, as specified: answer only from sources, `[n]` markers, navigation path first,
   concise, exact "I couldn't find this in the project documents" phrase).
6. Stream tokens via SSE. After completion: parse markers, drop/flag any `[n]` not in the provided set, and map
   valid markers to source cards. If the model replied with the not-found phrase or no valid citation → status `not_found`, UI shows
   the "Not found in documents" state + top related sources.

Answer SSE events: `sources` (sent first, so cards render immediately) → `token`* → `final` (validated citations, status).

---

## 5. Model management

`LLMProvider` interface → `OpenAICompatibleProvider` (httpx, streaming `/chat/completions`, `/models` health).
Ollama, llama.cpp server, vLLM, LM Studio and Bionic all speak this API, so adding one is a new row in `models`. Admin UI: add/edit,
"Send test prompt" (shows latency + first tokens), enable/disable, set default. Seeded from `.env`
(`QWEN_BASE_URL`, `QWEN_MODEL_NAME`, `QWEN_API_KEY`, `QWEN_CONTEXT_LENGTH`) on first migration.
The LLM client sets conservative timeouts and a concurrency semaphore (`LLM_MAX_CONCURRENCY`, default 2), so InfoPoint
doesn't flood the Qwen endpoint it shares with AiTrading.

---

## 6. Users & access

bcrypt (passlib), roles admin/user, first admin created from `.env` (`INITIAL_ADMIN_USER`/`PASSWORD`, forced change).
`DESKTOP_SINGLE_USER=true` → requests from 127.0.0.1 with the launcher's per-launch token are auto-logged in as the local admin.
CSRF: SameSite=Strict cookie + custom header check on mutating requests.
File streaming, viewer and search all call the same `allowed_project_ids(user)` helper.

---

## 7. Viewers

- `/view/pdf/:fileId?page=14&hl=<chunkId>` → bundled PDF.js (pdfjs-dist) viewer, opens at the page and highlights the cited text using the
  find controller with the chunk's leading phrase. If that fails (OCR pages), it falls back to drawing stored OCR boxes.
  Office files open as their derived PDF, with a "Download original" link.
- `/view/media/:fileId?t=3130` → `<video>`/`<audio>` with `#t=` + `currentTime`, transcript side panel with
  active-segment highlight, click-to-seek, keyboard controls.
- `GET /api/files/:id/content` (and `/proxy` for media) → access check + `Range` support (206), correct MIME, path
  traversal protection (resolved path must stay within the folder's container path).

---

## 8. UI

React + TS + Vite + Tailwind with design tokens from the brief (`#FAFAF7`, `#FFFFFF`, `#E7E5E0`, `#1F2328`,
`#6B6F76`, accent `#0F6E6E`). Font: **IBM Plex Sans** 400/600, self-hosted woff2. Icons: lucide-react (bundled,
file-type icons only, no sparkles). No CDN references. A CI check greps `dist/` for `http(s)://` outside an allowlist.

- **Ask**: left sidebar (project multi-select, new conversation, recent conversations) · centered reading column
  (question as a quiet heading, answer as rendered Markdown document) · right Sources panel (cards: icon, name,
  "Page 14" / "52:10–53:40", snippet, Open). Hover/focus on `[n]` ↔ card highlight. Empty state with 3 examples;
  "Not found in documents" state. Model picker per conversation (enabled models).
- **Library**: per-project table (file, type, size, status pill, progress bar, indexed at, error tooltip, re-index).
- **Settings** (admin): folders table + add/edit drawer (Browse… / server browser / path field, validation preview
  with counts, size, estimated time), general settings, embedding model + Re-index all, Bionic options.
  First-run: redirect to Settings with "Add your first document folder" guide.
- **Admin**: Models, Users (+ project assignment), Health (DB, LLM, worker heartbeat, queue length by lane,
  last scan per folder, recent Bionic events), Audit log.
- Responsive to ≥768 px (Sources panel collapses into a drawer), visible focus rings, AA contrast verified.

---

## 9. Desktop launcher & Bionic lifecycle

`launcher/` (pywebview + EdgeChromium/WebView2). State machine (pure, unit-testable):

```
CHECK_BIONIC ─ok→ (already_running=true) ──────────────┐
     └─fail→ START_BIONIC → POLL (≤ BIONIC_START_TIMEOUT) ─ok→ (started_by_us=true) ─┤
                                  └─timeout→ ERROR [Retry] [View logs] [Exit]        │
START_APP_SERVICES (compose up -d pg api worker; wait /api/health) → MAIN_WINDOW ◄──┘
on window closing → CloseDialog(started_by_us) → choice
```

- `close_dialog_model(started_by_us) -> {default, note}`, tested exhaustively.
- Stop Bionic: `BIONIC_STOP_CMD` (`docker compose stop`, never `down`/`-v`), then confirms the health URL is down.
- Stop app services: `POST /internal/shutdown` to the worker (finish the current job up to a 60 s grace period, else leave
  it for heartbeat-expiry resume) → `docker compose stop api worker` (postgres too in desktop mode).
- Crash → nothing stops Bionic. The next launch sees it healthy and records "already running".
- All actions logged (JSON lines, `logs/launcher.log`) and inserted into `bionic_events` when the DB is up.
- Native "Browse…" is exposed to the SPA via `window.pywebview.api.pick_folder()`. The SPA feature-detects it.

---

## 10. Offline packaging & operations

`scripts/prepare_offline.(sh|ps1)` (run online) produces `offline_bundle/`:
- `images/*.tar` — `docker save` of pgvector/pgvector:pg16, infopoint-api, infopoint-worker (built with all wheels,
  Tesseract (+ language packs from `OCR_LANGS`), LibreOffice, ffmpeg).
- `models/` — `BAAI/bge-m3` (HF snapshot), faster-whisper `small` (+ optionally `base`, `medium`).
- `launcher/InfoPoint.exe` (+ Linux build if needed), `frontend/dist` (also baked into the api image).
- `install_offline.(sh|ps1)` — `docker load`, copies models, writes `.env` from template, creates a desktop shortcut.

At runtime: `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, telemetry disabled, Docker network for the app has no egress
requirement. Test: `docker compose run --network none` smoke test + the CI CDN grep.

Logs: structlog JSON to stdout + rotating files (`ingest`, `query`, `bionic` event types). README covers setup,
config, adding a folder/root, adding a model, backup (`pg_dump`), troubleshooting.

---

## 11. Repository layout

```
InfoPoint/
├─ backend/
│  ├─ app/
│  │  ├─ main.py               # FastAPI app, static SPA, routers
│  │  ├─ config.py             # pydantic-settings (.env)
│  │  ├─ db/ (base.py, models.py, session.py)
│  │  ├─ api/ (auth, ask, conversations, folders, fsbrowse, library, files, viewer, models, users, settings, health, internal)
│  │  ├─ auth/ (provider.py, local.py, sessions.py, deps.py)
│  │  ├─ rag/ (retrieve.py, fusion.py, prompt.py, citations.py, budget.py)
│  │  ├─ llm/ (provider.py, openai_compatible.py, registry.py)
│  │  ├─ embed/ (embedder.py, client.py)
│  │  ├─ paths.py              # root aliases, host↔container mapping, traversal guards
│  │  └─ events.py             # LISTEN/NOTIFY → SSE
│  ├─ worker/
│  │  ├─ main.py               # lanes, claim loop, heartbeat, graceful shutdown
│  │  ├─ watcher.py  scanner.py  stability.py
│  │  ├─ jobs/ (index_file.py, delete_file.py, scan_folder.py, reindex_all.py, purge_folder.py)
│  │  └─ extract/ (pdf.py, ocr.py, office.py, sheet.py, text.py, media.py, chunker.py)
│  ├─ alembic/  alembic.ini
│  ├─ tests/   pyproject.toml   Dockerfile.api   Dockerfile.worker
├─ frontend/
│  ├─ src/ (pages/, components/, api/, styles/tokens.css, fonts/)
│  ├─ public/pdfjs/            # copied from pdfjs-dist at build
│  └─ package.json vite.config.ts tailwind.config.ts
├─ launcher/ (main.py, bionic.py, services.py, close_dialog.py, splash.html, tests/, InfoPoint.spec)
├─ scripts/ (prepare_offline.sh/.ps1, install_offline.sh/.ps1, dev.ps1)
├─ docker-compose.yml   docker-compose.roots.yml (generated from ALLOWED_DOC_ROOTS)   .env.example
├─ docs/ (PLAN.md, ARCHITECTURE.md)
└─ README.md
```

---

## 12. Phases

| Phase | Scope | Exit / demo |
|---|---|---|
| **1** | Compose (pg16+pgvector, api, worker), schema + Alembic, root mapping, Settings → folders (text path + server browser, validation preview, audit), watcher/scanner/stability gate, job queue, PDF (+OCR) and DOCX/PPTX via LibreOffice, chunking, bge-m3 embeddings, hybrid search + RRF, Ask API (SSE) with citation validation + retrieval gate, basic Ask/Library/Settings UI, seed Qwen model from `.env`, bootstrap admin login | Add a folder in the UI → PDFs indexed without restart. A question returns a streamed answer with `[n]` → "file, page N". Edit/delete a file → answers update. Unanswerable question → not-found. |
| **2** | ffmpeg + faster-whisper media lane, transcript segments, time chunks, media proxy/transcode, PDF.js viewer with page + highlight, media viewer with transcript sync, range streaming, XLSX/CSV/TXT/MD extractors | 1-hour video indexed. "Play from 52:10" starts at 52:10. "Open page 14" opens at 14 with highlight. |
| **3** | Models table UI + test prompt + per-conversation picker, provider registry, users/roles/project assignments, enforced filtering everywhere, conversations history, single-user desktop mode | New model added from Admin and used with no code change. User B can't retrieve or open project A files. |
| **4** | Launcher + Bionic lifecycle + close dialog, design polish to spec, Health page, offline scripts + install, structured logs, README, CDN/egress checks | Cable unplugged → full flow works from the desktop shortcut. All acceptance criteria pass. |

## 13. Tests (pytest; Postgres via the compose service; vitest for UI helpers)

- **Chunk location metadata**: fixture PDF (text + scanned page + screenshot), DOCX, PPTX, XLSX, MD, short generated
  audio (ffmpeg TTS-free sine + known WAV fixture with speech), checking page/slide/rows/lines/timestamps and that chunks never cross pages.
- **File lifecycle**: add → indexed; modify (hash change) → old chunks gone, new present, atomic; same-hash touch → no
  re-index; delete → chunks gone; partially-copied file waits for stability.
- **Folders**: add (outside roots rejected, traversal rejected), change path (old chunks purged, new indexed),
  disable (hidden from search, chunks kept), remove (purged), audit rows written.
- **Citations**: out-of-range markers stripped, `[1][3]`/`[1, 2]` forms parsed, not-found detection, gate behaviour.
- **Access filtering**: retrieval, file streaming, viewer and library endpoints for users with/without project access.
- **Close dialog logic**: started-by-us vs already-running defaults + note, stop command invoked only on that choice,
  never `down -v`, timeout/retry path of the start state machine.
- **Queue**: SKIP LOCKED concurrency, lane isolation (media job running, doc job still processed), stale-heartbeat resume.

---

## 14. Risks & open questions

1. **Values needed**: `BIONIC_COMPOSE_DIR`, `QWEN_BASE_URL`, `QWEN_MODEL_NAME`, whether the Bionic endpoint needs an
   API key, and the served context length for Qwen. Bionic is not currently running or listed in `docker compose ls` on this machine.
2. **Server-mode target**: Windows or Linux, CPU or GPU? (This machine: Windows 11, Core 7 150U, 16 GB, no NVIDIA GPU, so
   desktop mode will be CPU-only, Whisper `small` int8.)
3. **UNC paths in Docker Desktop**: `\\fileserver\share` can't be bind-mounted directly. Plan: a CIFS named volume per UNC root
   (credentials in `.env`) or map the share to a drive letter on the host. Which is acceptable?
4. **Document languages**: English only, or also other languages? This affects the Tesseract language packs, the FTS config
   (`english` vs `simple`) and whether to keep bge-m3 (multilingual, recommended).
5. **Answer quality with Qwen 1.5B**: the gate and citation validation guard correctness, but answer fluency will be limited.
   The model-management design makes swapping to a larger local model a config change.
