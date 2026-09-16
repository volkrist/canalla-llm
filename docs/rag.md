# Local RAG — 0.6.0

Documents are a separate subsystem from chat history and personal memory. No RunPod or paid embedding API is used.

## Embeddings

CPU model: `intfloat/multilingual-e5-small`, MIT, 384 dimensions, multilingual (including Russian, English and Korean). Quantized ONNX conversion: `Xenova/multilingual-e5-small`, pinned revision `761b726dd34fb83930e26aab4e9ac3899aa1fa78`, file `onnx/model_quantized.onnx`. FastEmbed 0.8 loads it with mean pooling, L2 normalization and CPUExecutionProvider. Queries use `query: `; documents use `passage: `. No remote Python code runs.

Primary references: [model card](https://huggingface.co/intfloat/multilingual-e5-small), [ONNX conversion](https://huggingface.co/Xenova/multilingual-e5-small), [FastEmbed custom models](https://github.com/qdrant/fastembed).

Prepare explicitly from `apps/backend`:

```powershell
.venv\Scripts\python.exe -m app.documents.embedding
```

Normal inference never downloads the model. Prepare explicitly in Files/Settings or the CLI; EmbeddingModelManager owns download/verification/activation in the application data root. `EMBEDDING_MODEL_DIR` is only a legacy import source; `EMBEDDING_MODEL_NAME` validates the supported architecture. Other architectures require a provider implementation and reindex. `EMBEDDING_THREADS=2` limits CPU threads. Missing/corrupt model fails indexing with a sanitized error; existing chats remain usable without retrieved documents. Allow roughly 0.5–1 GB RAM for inference plus bounded indexing data. Document binaries remain in gitignored `.data/`; model installations use the shared application data directory. See [model lifecycle](embedding-model-manager.md).

## Chunking and retrieval

The model accepts 512 tokens. Chunks therefore target **450 actual tokenizer tokens**, rather than the originally suggested 800–1200 which would truncate this model. Adjacent paragraphs within a page/heading are combined; long sections prefer sentence/paragraph boundaries and retain approximately 64 tokens overlap. Page and heading metadata are retained. Query embedding uses a bounded prefix of a long prompt; the complete current user message still reaches the LLM once.

`EmbeddingProvider`, `DocumentChunker`, `VectorStore` and `DocumentIndexJob` isolate implementation choices. `SQLVectorStore` keeps normalized vectors in SQL JSON alongside chunks and computes exact cosine similarity in a streaming scan. Ownership and project scope are applied **in SQL before reading vectors**. Default top-k 6, similarity threshold 0.72; project sources precede general sources within the selected results. There is no hybrid score. Thresholds require tuning for real documents; cosine similarity is not a probability.

General chat searches only general documents. Project chat searches its project plus general documents when enabled. It never searches another user's files or another project. Normal admin endpoints have no bypass.

Per-user RAG preferences are persisted: enabled, include_general, max_chunks (1–12), max_chars (default 12,000, server maximum `RAG_MAX_CHARS` up to 20,000), similarity threshold. The document content budget is independent of memory/history budgets. Source wrappers add bounded metadata outside excerpt character counts.

Context order: system → profile/instructions → pinned memory → project → project memory → general memory → document excerpts → web/tool references → recent history → current prompt. Excerpts are explicitly **untrusted reference material**, in a user-context section, never a system message. This preserves instruction hierarchy; it is not a guarantee that every language model resists every prompt injection.

## Sources and history

The backend labels actual selected chunks D1, D2, etc. (historical S labels remain valid). Web sources use separate W labels. It stores a generation snapshot in `message_contexts.snapshot`: document/chunk IDs, original display title, page/heading, project, upload time, rank, similarity, label and the bounded excerpt delivered to the provider. Old answers do not rerun retrieval. The panel lists **sources passed to the model**, not a claim that every source supports the answer. Inline citation rendering is deferred; model text is never rewritten to fabricate citations.

Deleting a document removes its binary/chunks/vectors and redacts excerpts from existing snapshots; title/page metadata remains with “Источник был удалён”. New generations cannot retrieve it. Reindex builds a replacement before transactionally swapping chunks; failed reindex preserves the previous index and reports failed status. A stale job after backend restart becomes retryable failed.

## Limits and deployment

Use one backend worker, as required by the existing compute/chat lifecycle. Local indexing is serialized; metadata mutation and source snapshot persistence are coordinated. This MVP does not add a distributed queue, OCR, cloud storage or pgvector. PostgreSQL-compatible schema is retained; a future pgvector implementation replaces VectorStore. Exact scans suit small personal corpora and are not a large-scale vector database.

Real CPU embedding/indexing and MockLLM source delivery are tested locally. Real OrcaRouter answer quality, citation fidelity, latency and cancellation with RAG require a separately authorized paid E2E later.
