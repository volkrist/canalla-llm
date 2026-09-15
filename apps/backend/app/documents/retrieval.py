from datetime import timezone

from sqlalchemy import or_, select

from ..models import Project
from .chunking import DocumentChunker
from .embedding import EmbeddingUnavailable, get_embedding
from .models import Document
from .routes import preferences
from .vector import SQLVectorStore


def retrieve(db, user, chat, prompt):
    options = preferences(db, user.id)
    if not options.enabled or not prompt.strip():
        return [], None
    scope = Document.project_id == chat.project_id if chat.project_id else Document.project_id.is_(None)
    if chat.project_id and options.include_general:
        scope = or_(scope, Document.project_id.is_(None))
    if not db.scalar(
        select(Document.id)
        .where(Document.user_id == user.id, scope, Document.indexed_at.is_not(None))
        .limit(1)
    ):
        return [], None
    try:
        embedding = get_embedding()
        query = prompt[: DocumentChunker(embedding.token_count).prefix(prompt, 450)]
        vector = embedding.embed([query], query=True)[0]
        results = SQLVectorStore().search(
            db,
            user.id,
            chat.project_id,
            vector,
            embedding.version,
            embedding.dimension,
            options.max_chunks,
            options.similarity_threshold,
            options.include_general,
        )
    except (EmbeddingUnavailable, ValueError):
        return [], "Embedding service unavailable. Ответ сформирован без документов."
    sources, size = [], 0
    for chunk, document, similarity in results:
        if size + len(chunk.content) > options.max_chars:
            continue
        size += len(chunk.content)
        project_name = (
            db.scalar(
                select(Project.name).where(Project.id == document.project_id, Project.user_id == user.id)
            )
            if document.project_id
            else None
        )
        sources.append(
            {
                "document_id": document.id,
                "display_name": document.display_name,
                "chunk_id": chunk.id,
                "page_number": chunk.page_number,
                "section_title": chunk.section_title,
                "project_id": document.project_id,
                "project_name": project_name,
                "uploaded_at": document.created_at.replace(tzinfo=timezone.utc).isoformat(),
                "rank": len(sources) + 1,
                "similarity": similarity,
                "label": f"S{len(sources) + 1}",
                "excerpt": chunk.content,
                "deleted": False,
            }
        )
    return sources, None
