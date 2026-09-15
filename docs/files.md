# Files — 0.5.0

Use **Файлы** or **Прикрепить файл** in the composer. Choose the project before uploading. General documents are available to general chats and optionally project chats. After Ready, the document participates automatically in the current scope; attachments are persistent documents, not ephemeral messages. A selected-file-only mode is deferred.

Supported: text PDF, DOCX, UTF-8 TXT and Markdown. No OCR, arbitrary ZIP, CSV or JSON ingestion. Default file limit 25 MiB; configurable downward with DOCUMENT_MAX_BYTES. Defaults: 100 documents/user, 500,000 extracted characters, 1,000 chunks/document. Server validation includes extension/MIME/signature, generated storage keys, per-user SHA-256 duplicate prevention, quota and multipart request byte limits. Filenames are never filesystem paths. Multipart parsers may normalize Windows client path names to their basename; remaining path separators are rejected.

`DocumentStorage` / `LocalDocumentStorage` stores binaries outside SQL under random keys. SQL records document metadata and owner/project scope. The files API does not expose storage paths, hashes or vectors. The Files screen supports title search, project/type/status filters, rename, reindex and deletion confirmation.

Index states: Uploaded/queued → Extracting → Chunking → Embedding → Ready, or Failed with a sanitized reason. No invented percentage. PDF retains page numbers; DOCX retains headings/paragraphs and table text. UTF-8 errors fail rather than silently corrupt text. Extractors run in a separate process with a 45-second timeout and a 512 MiB process-memory ceiling (Windows Job Object / Unix RLIMIT_AS), page/character limits; DOCX ZIP entries, expansion size and ratios are checked. This is a resource boundary, not an antivirus sandbox.

Delete removes binary, chunks and vectors; old answer metadata remains but its excerpt is cleared. Reindex is atomic and preserves the previous index if extraction/embedding fails. Source viewer shows delivered excerpt, page/heading, project and upload date.

Data schema: `documents` (owner/project, original/display names, MIME/extension, size/key/hash, status/error/job, timestamps, chunk/text counts), `document_chunks` (owner/project/document, index/content/hash/character and token count, page/heading, model/version/dimension/vector, timestamp), `rag_preferences`. IDs and foreign keys remain compatible with PostgreSQL.

Local paths default to `.data/documents` and `.data/embeddings/e5-small`; do not commit either. Back up the database and document directory together. Runtime never provisions compute. See [RAG](rag.md) for CPU model preparation, budgets and retrieval limitations.
