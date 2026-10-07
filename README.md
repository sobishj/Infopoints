# InfoPoint

Offline project knowledge assistant. It answers questions from developers and business analysts using **only**
the documents in folders you choose, and cites every statement with the file and page.

> Go to Procurement → Pending Orders → select the order → Approve **[1]**
> **[1]** Procurement_User_Guide.pdf · Page 14 · *Open page 14*

Everything runs locally: PostgreSQL + pgvector, a FastAPI backend, an ingestion worker, and the local Qwen model
served by **Bionic**. Nothing is sent to the internet at runtime.

**Status: Phase 1** (folders, ingestion of PDF / Word / PowerPoint / text, hybrid search, cited answers, basic UI).
Audio/video, the in-app PDF/media viewer, model and user admin, and the desktop launcher follow in Phases 2–4
(see [docs/PLAN.md](docs/PLAN.md)).

---

## Quick start (Windows, this machine)

Requirements: Docker Desktop, Bionic (with `qwen2.5-1.5b-instruct`), Python 3 on the host (for one small script).

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start.ps1
```

The script:

1. creates `.env` on first run (random secrets; the admin password is printed and stored in `.env`),
2. sets the default document folder to your **Downloads** folder,
3. starts Bionic if it isn't serving yet (it is left running afterwards; AiTrading shares it),
4. builds the Docker images and downloads the embedding model (first run only, ~2.3 GB, needs internet once),
5. starts `db`, `api` and `worker`, and opens <http://localhost:8765>.

On first start InfoPoint asks you to choose a document folder. If you skip, it indexes your Downloads folder.
Desktop mode signs you in automatically (`AUTO_LOGIN=true`; the port is bound to 127.0.0.1 only).

Stop: `scripts\stop.ps1` (the worker finishes its current file or resumes it next time; Bionic keeps running).
After code changes: `scripts\start.ps1 -Build`.

## Adding a document folder

Settings → **Add folder** → type or paste a path (or **Browse…**) → **Check folder** (shows file counts, size and an
indexing-time estimate) → **Save and start indexing**. The watcher picks the folder up immediately; the Library page
shows progress per file. New, changed and deleted files are handled automatically (changes are noticed within about
20 seconds, plus a full rescan every 5 minutes by default).

InfoPoint can only read folders under `ALLOWED_DOC_ROOTS` (default `C:\`). Roots are mounted **read-only** into the
containers at `/roots/<alias>`. To allow another drive, append it to `ALLOWED_DOC_ROOTS` in `.env`
(comma-separated, e.g. `C:\,D:\Projects`) and run `scripts\start.ps1` again. Network shares (`\\server\share`) can't
be bind-mounted by Docker Desktop directly: map them to a drive letter first.

Supported now: PDF (with OCR for scanned pages and screenshots), DOCX/DOC/ODT/RTF and PPTX/PPT/ODP (converted with
LibreOffice so citations carry real page/slide numbers), TXT and Markdown.

## Configuration (`.env`)

| Setting | Default | Meaning |
|---|---|---|
| `ALLOWED_DOC_ROOTS` | `C:\` | Where document folders may live (host paths, comma-separated) |
| `DEFAULT_DOC_FOLDER` | your Downloads | Used when first-run folder selection is skipped |
| `QWEN_BASE_URL` | `http://host.docker.internal:1234/v1` | Bionic's OpenAI-compatible endpoint, as seen from Docker |
| `QWEN_MODEL_NAME` | `qwen2.5-1.5b-instruct` | Model id served by Bionic |
| `QWEN_CONTEXT_LENGTH` | `32768` | Context window Bionic loads the model with |
| `LLM_MAX_CONCURRENCY` | `2` | Max simultaneous requests InfoPoint sends to the shared model |
| `EMBEDDING_MODEL` | `BAAI/bge-m3` | Changing it requires re-indexing all files |
| `RESCAN_INTERVAL_SECONDS` | `300` | Full rescan interval (also editable in Settings) |
| `MIN_VECTOR_SIMILARITY` | `0.45` | Below this the app answers "couldn't find" without asking the model |
| `AUTO_LOGIN` | `true` | Desktop single-user mode |

The model seeded on first start is the local Qwen in Bionic. Model management in the UI arrives in Phase 3; until
then the `models` table can be edited directly.

## How answers are produced

1. Hybrid search over the selected projects: pgvector cosine similarity (HNSW) + Postgres full-text search, merged
   with reciprocal rank fusion. Project permissions are applied in SQL.
2. If nothing relevant is found, InfoPoint replies "I couldn't find this in the project documents" without calling
   the model.
3. Otherwise the top passages (with `[Source: file | Page N]` headers) go to Qwen with strict instructions to answer
   only from them and cite `[n]`. The answer streams to the page.
4. Citation markers that don't refer to a provided passage are removed before the answer is saved.

## Tests

```powershell
docker compose run --rm --no-deps -v ${PWD}/backend:/app worker pytest -q
```

Tests use a separate `infopoint_test` database and a deterministic fake embedder; LibreOffice tests are marked `slow`
(`-m "not slow"` skips them).

## Layout

```
backend/app      FastAPI app: api/, auth/, rag/, llm/, embed/, folders.py, jobs.py, rootmap.py
backend/worker   ingestion worker: watcher, scanner, extractors, chunker, job lanes
backend/alembic  database migrations (PostgreSQL 16 + pgvector)
frontend/        React + TypeScript + Vite + Tailwind (fonts and assets bundled; no CDNs)
scripts/         start / stop / model download / compose root generation
```

Logs are JSON lines: `docker compose logs -f api worker`.
