import hashlib
import threading

from sqlalchemy import select, update

from ..config import get_settings
from ..database import SessionLocal
from ..models import now
from .chunking import DocumentChunker
from .embedding import EmbeddingUnavailable, get_embedding
from .extract import ExtractionError, LocalExtractor
from .models import Document
from .storage import LocalDocumentStorage
from .vector import DocumentChunk, SQLVectorStore

mutation_lock = threading.RLock()
index_lock = threading.Lock()


class DocumentIndexJob:
    def __init__(self, settings=None, embedding=None, extractor=None):
        self.settings = settings or get_settings()
        self.embedding = embedding or get_embedding()
        self.extractor = extractor or LocalExtractor()

    def phase(self, key, job, state):
        with mutation_lock, SessionLocal() as db:
            result = db.execute(
                update(Document)
                .where(Document.id == key, Document.job_id == job)
                .values(status=state, updated_at=now())
            )
            db.commit()
            return result.rowcount == 1

    def run(self, key, job):
        with index_lock:
            try:
                if not self.phase(key, job, "extracting"):
                    return
                with SessionLocal() as db:
                    row = db.get(Document, key)
                    if not row or row.job_id != job:
                        return
                    storage_key, extension = row.storage_key, row.extension
                parts = self.extractor.extract(
                    LocalDocumentStorage(self.settings.document_storage_dir).path(storage_key),
                    extension,
                    self.settings.document_max_chars,
                )
                if not self.phase(key, job, "chunking"):
                    return
                chunks = DocumentChunker(self.embedding.token_count).chunk(
                    parts, self.settings.document_max_chunks
                )
                if not self.phase(key, job, "embedding"):
                    return
                vectors = self.embedding.embed([chunk["content"] for chunk in chunks])
                with mutation_lock, SessionLocal() as db:
                    row = db.scalar(
                        select(Document).where(Document.id == key, Document.job_id == job).with_for_update()
                    )
                    if not row:
                        return
                    store = SQLVectorStore()
                    store.delete_document(db, row.user_id, key)
                    store.upsert(
                        db,
                        [
                            DocumentChunk(
                                document_id=key,
                                user_id=row.user_id,
                                project_id=row.project_id,
                                chunk_index=index,
                                content=chunk["content"],
                                content_hash=hashlib.sha256(chunk["content"].encode()).hexdigest(),
                                char_count=len(chunk["content"]),
                                token_count=chunk["token_count"],
                                page_number=chunk["page_number"],
                                section_title=chunk["section_title"],
                                embedding=vector,
                                embedding_model=self.embedding.version,
                                embedding_dimension=self.embedding.dimension,
                            )
                            for index, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True))
                        ],
                    )
                    row.status, row.error_message, row.job_id = "ready", None, None
                    row.indexed_at = row.updated_at = now()
                    row.chunk_count, row.text_char_count = len(chunks), sum(len(p["content"]) for p in parts)
                    db.commit()
            except Exception as error:
                reason = (
                    str(error)
                    if isinstance(error, (ExtractionError, EmbeddingUnavailable))
                    else "Индексация не выполнена: проверьте формат и лимиты документа. Можно повторить."
                )
                with mutation_lock, SessionLocal() as db:
                    db.execute(
                        update(Document)
                        .where(Document.id == key, Document.job_id == job)
                        .values(status="failed", error_message=reason[:300], job_id=None, updated_at=now())
                    )
                    db.commit()


def reconcile_jobs():
    with SessionLocal() as db:
        db.execute(
            update(Document)
            .where(Document.job_id.is_not(None))
            .values(
                status="failed",
                error_message="Индексация прервана перезапуском backend. Нажмите «Переиндексировать».",
                job_id=None,
            )
        )
        db.commit()
