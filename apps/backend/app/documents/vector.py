from abc import ABC, abstractmethod

import numpy as np
from sqlalchemy import delete, or_, select

from .embedding import validate_vectors
from .models import Document, DocumentChunk


class VectorStore(ABC):
    @abstractmethod
    def upsert(self, db, chunks): ...

    @abstractmethod
    def delete_document(self, db, user_id, document_id): ...

    @abstractmethod
    def search(
        self, db, user_id, project_id, vector, model, dimension, top_k, threshold, include_general
    ): ...


class SQLVectorStore(VectorStore):
    """Bounded local exact cosine scan. Ownership/scope applied in SQL before vectors are read."""

    def upsert(self, db, chunks):
        for chunk in chunks:
            validate_vectors([chunk.embedding], 1, chunk.embedding_dimension)
            db.add(chunk)

    def delete_document(self, db, user_id, document_id):
        db.execute(
            delete(DocumentChunk).where(
                DocumentChunk.user_id == user_id, DocumentChunk.document_id == document_id
            )
        )

    def search(
        self, db, user_id, project_id, vector, model, dimension, top_k=6, threshold=0.72, include_general=True
    ):
        if not user_id:
            raise ValueError("User scope required")
        query = np.asarray(validate_vectors([vector], 1, dimension)[0])
        scope = DocumentChunk.project_id == project_id if project_id else DocumentChunk.project_id.is_(None)
        if project_id and include_general:
            scope = or_(scope, DocumentChunk.project_id.is_(None))
        statement = (
            select(DocumentChunk, Document)
            .join(Document)
            .where(
                DocumentChunk.user_id == user_id,
                Document.user_id == user_id,
                scope,
                Document.indexed_at.is_not(None),
                DocumentChunk.embedding_model == model,
                DocumentChunk.embedding_dimension == dimension,
            )
        )
        best = []
        for chunk, document in db.execute(statement.execution_options(yield_per=100)):
            embedding = np.asarray(chunk.embedding)
            similarity = float(embedding @ query)
            if similarity >= threshold:
                best.append((chunk, document, similarity))
                best.sort(key=lambda row: (-row[2], row[0].id))
                del best[top_k:]
        # Project context first, preserving similarity rank within each scope.
        best.sort(key=lambda row: (row[0].project_id != project_id, -row[2], row[0].id))
        return best
