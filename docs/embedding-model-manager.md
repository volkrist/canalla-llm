# Embedding model lifecycle

The backend-wide EmbeddingModelManager owns preparation; the provider never downloads.
Open Files or Settings → Files / RAG and choose Prepare. Opening the main app does not
download anything. Chat, memory and compute remain independent of model availability.

Location: ALEX_LLM_DATA_DIR, otherwise the operating-system application-data directory:
Windows %LOCALAPPDATA%/Alex LLM; macOS ~/Library/Application Support/Alex LLM;
Linux $XDG_DATA_HOME/alex-llm (default ~/.local/share/alex-llm).
Documents retain their existing configured storage location.

Pinned artifacts: Xenova/multilingual-e5-small at
761b726dd34fb83930e26aab4e9ac3899aa1fa78; 135392183 bytes. The manager verifies
the LFS SHA-256 digests and Git blob SHA-1 digests recorded in model_manifest.py.
Official huggingface_hub downloads into staging and owns resumable cache files.
Its tqdm_class reports actual transferred/resumed bytes. Verified existing files
count toward completed bytes; no elapsed-time progress is invented.

One filesystem download lock coordinates processes/users; state updates and active
installation pointers use atomic replacement. Preparation requires three times artifact
size plus 64 MiB free space. Incomplete staging never becomes active. A restart with
an abandoned lock exposes CANCELLED and permits resume; corrupt complete files are
discarded before retry. A valid old installation is preserved.

Activation requires all hashes and real CPU query/passage embeddings with finite,
nonzero 384-dimensional vectors. Existing installations are checked on first access
and whenever artifact stat metadata changes. Runtime remains offline.
Verified legacy repo caches can be imported without re-downloading.

Authenticated API: GET /rag/model, GET /rag/model/events (SSE),
POST /rag/model/prepare, /retry, /cancel. Preparation is shared; cancellation belongs
to the initiator or an administrator. No model deletion endpoint is exposed.
Cancellation is cooperative at Hugging Face progress callbacks or file boundaries;
the UI distinguishes a cancellation request from completed cancellation.

The model and cache are neither tracked nor included in installers. The legacy
python -m app.documents.embedding command delegates to the same lifecycle manager.
