from fastapi import APIRouter

from sonder.api.schemas import CreateMessage
from sonder.application.messages import MessageService

router = APIRouter(prefix="/api", tags=["messages"])
service = MessageService()


@router.get("/projects/{project_id}/messages")
def list_messages(project_id: str):
    return service.list_messages(project_id)


@router.post("/projects/{project_id}/messages", status_code=201)
def save_message(project_id: str, body: CreateMessage):
    return service.save_message(project_id, body)


@router.post("/projects/{project_id}/messages/{message_id}/retry")
def retry_message(project_id: str, message_id: str):
    return service.retry_message(project_id, message_id)
