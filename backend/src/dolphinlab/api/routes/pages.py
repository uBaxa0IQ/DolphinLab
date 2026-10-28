"""Trusted workspace page editing; agents use separately authorized runtime tools."""

import json

from fastapi import APIRouter, Response

from dolphinlab.application.pages import PageService
from dolphinlab.domain.pages import PageFeedbackChange, PagePublish, PageSave

router = APIRouter(prefix="/api/projects/{project_id}/pages", tags=["pages"])
service = PageService()


@router.get("")
def pages(project_id: str):
    return service.index(project_id)


@router.post("")
def save_page(project_id: str, body: PageSave):
    return service.save(project_id, body)


@router.get("/catalog")
def catalog(project_id: str):
    return service.catalog(project_id)


@router.get("/{page_id}")
def read_page(project_id: str, page_id: str):
    return service.read(project_id, page_id)


@router.post("/{page_id}/publish")
def publish_page(project_id: str, page_id: str, body: PagePublish):
    return service.publish(project_id, page_id, body)


@router.post("/{page_id}/feedback")
def page_feedback(project_id: str, page_id: str, body: PageFeedbackChange):
    return service.feedback(project_id, page_id, body)


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
