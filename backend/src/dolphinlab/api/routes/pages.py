"""Trusted workspace page editing; agents use separately authorized runtime tools."""

import json

from fastapi import APIRouter, Response

from dolphinlab.application.pages import PageService
from dolphinlab.domain.pages import (
    EditorialSave,
    PageFeedbackChange,
    PagePublish,
    PageSave,
    StartEditor,
)

router = APIRouter(prefix="/api/projects/{project_id}/pages", tags=["pages"])
service = PageService()


@router.get("")
def pages(project_id: str):
    return service.index(project_id)


@router.post("")
def save_page(project_id: str, body: PageSave | EditorialSave):
    return service.save(project_id, body)


@router.get("/catalog")
def catalog(project_id: str):
    return service.catalog(project_id)


@router.get("/editor")
def editor_status(project_id: str):
    from dolphinlab.application.editor import EditorService

    return EditorService().status(project_id)


@router.post("/editor")
def start_editor(project_id: str, body: StartEditor):
    from dolphinlab.application.editor import EditorService

    return EditorService().queue(
        project_id, str(body.run_id), str(body.client_id), instruction=body.instruction
    )


@router.get("/{page_id}")
def read_page(project_id: str, page_id: str):
    return service.read(project_id, page_id)


@router.post("/{page_id}/publish")
def publish_page(project_id: str, page_id: str, body: PagePublish):
    return service.publish(project_id, page_id, body)


@router.post("/{page_id}/feedback")
def page_feedback(
    project_id: str,
    page_id: str,
    body: PageFeedbackChange,
    expected_page_revision: int | None = None,
):
    return service.feedback(
        project_id, page_id, body, expected_page_revision=expected_page_revision
    )


@router.get("/{page_id}/publications/{release_id}")
def publication(project_id: str, page_id: str, release_id: str):
    return service.publication(project_id, page_id, release_id)


@router.get("/{page_id}/export")
def export_page(project_id: str, page_id: str, release_id: str | None = None):
    export = service.export(project_id, page_id, release_id)
    return Response(
        json.dumps(export, ensure_ascii=False),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="page-{page_id}.json"',
            "X-Content-Type-Options": "nosniff",
        },
    )
