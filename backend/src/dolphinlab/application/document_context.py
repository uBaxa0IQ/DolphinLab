"""Explicit source selection and bounded, versioned document context."""

from fastapi import HTTPException
from sqlalchemy import select

from dolphinlab.infrastructure.database import Document, DocumentVersion


def document_index(db, project_id):
    rows = db.execute(
        select(DocumentVersion, Document)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(
            Document.project_id == project_id,
            Document.deleted.is_(False),
            DocumentVersion.state == "ready",
        )
        .order_by(DocumentVersion.created.desc())
        .limit(200)
    ).all()
    return [
        {
            "version_id": v.id,
            "document_id": d.id,
            "name": v.name,
            "revision": v.revision,
            "sha256": v.sha256,
            "current": d.revision == v.revision,
            "warnings": v.warnings,
        }
        for v, d in rows
    ]


def selected_documents(db, project_id, sources):
    from .documents import DocumentService

    output, seen = [], set()
    for source in sources:
        ident = str(source.chunk_id)
        if ident in seen:
            raise HTTPException(400, "Повторная ссылка на документный фрагмент")
        seen.add(ident)
        item = DocumentService().read(project_id, "chunk:" + ident, db=db)
        if item["version_id"] != str(source.version_id) or item["sha256"] != source.sha256:
            raise HTTPException(409, "Версия или SHA256 документного источника не совпадает")
        output.append({**item, "reason": source.reason})
    return output


def current_document_ids(db, project_id):
    return set(
        db.scalars(
            select(DocumentVersion.id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(
                Document.project_id == project_id,
                Document.deleted.is_(False),
                DocumentVersion.state == "ready",
                DocumentVersion.revision == Document.revision,
            )
        )
    )
