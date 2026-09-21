# Backend sidecar packaging audit (0.9.3)

Inspected runtime imports, Alembic, FastAPI lifespan, RAG, TinyFish Browser, Tor, and Desktop host — not pyproject alone.

## Must ship in the backend sidecar

| Surface | Why |
|---|---|
| FastAPI + Starlette + Uvicorn (httptools, websockets) | HTTP + SSE + `/ws/presence` |
| SQLAlchemy + Alembic + `alembic.ini` + `alembic/versions/*` | startup migrate to head |
| Pydantic / pydantic-settings | config |
| PyJWT + pwdlib/argon2 + email-validator | auth |
| httpx / httpcore / anyio | RunPod + TinyFish HTTP |
| python-multipart, pypdf, python-docx | files |
| filelock | embedding install lock |
| `app/**` including `compute/remote_runtime.py` | remote llama.cpp bootstrap is read as bytes, not only imported |
| `app.runtime_entry` | Desktop-owned entry |

SQLite is the product database. `psycopg` is a declared dependency and should travel with the sidecar so optional URLs do not crash import; it is not required at default startup.

## Optional / lazy — libraries yes, payloads no

| Surface | Ship library? | Ship payload? |
|---|---|---|
| FastEmbed + ONNX Runtime + tokenizers + numpy | Yes (RAG must not silently vanish) | **No** ~118MB `model_quantized.onnx` / tokenizer |
| huggingface-hub | Yes (prepare path) | No Hugging Face cache |
| Playwright Python | Optional; import is lazy inside TinyFish CDP connect | **No Chromium** |
| Tor SOCKS / Marionette | Yes (stdlib sockets + local Firefox profile) | No Tor Browser / Firefox binaries |
| RunPod | Yes | No GGUF, no volume data, no API keys |

Embeddings live at `%LOCALAPPDATA%\Alex LLM\models\embeddings\`. `/rag/model` already reports `NOT_INSTALLED` until hashes+smoke pass. Chat without RAG does not load ONNX.

Playwright is **not** imported at FastAPI startup. `make_registry()` constructs TinyFish browser providers; Chromium/`async_playwright` runs only when connecting to a TinyFish CDP session. WM-07 remains a known limitation and is not bundled here.

## Desktop / native host — not inside the Python sidecar

The Tauri `alex-llm.exe` already executes host jobs via IPC (`pair_device`, `execute_host_jobs`, …). `alex-host-loop.exe` is the same modules without a WebView (E2E / headless). The installer must ship both; neither may depend on the git checkout.

## Must not bundle

- Production GGUF (~20GB) / Network Volume `uwgeaie5b0`
- Provider secrets, user DB, JWT, documents, embedding cache
- Playwright browsers, Tor Browser
- Git, Node, system Python, repo `.venv`

## Startup resource contract

Immutable (next to / inside sidecar): Alembic scripts, package code, `remote_runtime.py`.

Mutable (`%LOCALAPPDATA%\Alex LLM\`): `data/alex.db`, `runtime/jwt.secret`, `runtime/shutdown.token`, documents, embedding cache, logs.

`runtime_entry.migrate()` uses `app.packaging.alembic_config()`, which points Alembic at the frozen resource root (`sys._MEIPASS` / `ALEX_APP_RESOURCE_DIR`) with an absolute `script_location`. Developer checkouts still resolve next to `apps/backend`.
