"""Explicit source selection and bounded, versioned document context."""

from fastapi import HTTPException
from sqlalchemy import select

from dolphinlab.infrastructure.database import Document, DocumentVersion, ResearchObject


def document_index(db, project_id, *, include_pending=False):
    rows = db.execute(
        select(DocumentVersion, Document)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(
            Document.project_id == project_id,
            Document.deleted.is_(False),
            DocumentVersion.state.in_(["queued", "processing", "ready", "failed"])
            if include_pending
            else DocumentVersion.state == "ready",
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
            "state": v.state,
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


def run_source_freshness(db, run):
    versions = {s["version_id"] for s in (run.project_context or {}).get("document_sources", [])}
    for evidence in db.scalars(
        select(ResearchObject.data).where(
            ResearchObject.run_id == run.id, ResearchObject.kind == "document_evidence"
        )
    ):
        versions.add(evidence["version_id"])
    stale = sorted(versions - current_document_ids(db, run.project_id))
    return {
        "current": not stale,
        "stale_document_version_ids": stale,
        "notice": "Historical evidence is retained; changed document definitions require an applicability review, not an automatic change of scientific verdict.",
    }
