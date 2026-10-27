"""Document ingestion, exact sources and project-scoped retrieval."""

from uuid import UUID

from fastapi import APIRouter, File, Form, Query, UploadFile

from dolphinlab.application.documents import DocumentService
from dolphinlab.domain.documents import DocumentControl, RetryDocument, SearchKnowledge

router = APIRouter(prefix="/api/projects/{project_id}", tags=["documents"])
service = DocumentService()


@router.get("/documents")
def list_documents(project_id: str):
    return service.list(project_id)


@router.post("/documents", status_code=201)
async def upload_document(
    project_id: str,
    file: UploadFile = File(...),
    client_id: UUID = Form(...),
    expected_revision: int = Form(0, ge=0),
    document_id: UUID | None = Form(None),
):
    return await service.upload(
        project_id, file, client_id, expected_revision, str(document_id) if document_id else None
    )


@router.get("/documents/versions/{version_id}/original")
def download_original(project_id: str, version_id: str):
    return service.download(project_id, version_id)


@router.post("/documents/retry")
def retry_document(project_id: str, body: RetryDocument):
    return service.retry(project_id, str(body.version_id))


@router.delete("/documents/{document_id}")
def remove_document(project_id: str, document_id: str, body: DocumentControl):
    return service.remove(project_id, document_id, body.expected_revision)


@router.post("/knowledge/search")
def search_knowledge(project_id: str, body: SearchKnowledge):
    return service.search(project_id, body)


@router.get("/knowledge/sources/{source_id}")
def read_source(project_id: str, source_id: str, neighbors: int = Query(0, ge=0, le=2)):
    return service.read(project_id, source_id, neighbors)
