from fastapi import APIRouter

from dolphinlab.api.schemas import CreateProject, EditProject
from dolphinlab.application.projects import ProjectService
from dolphinlab.domain.policy import ProjectPolicy

router = APIRouter(prefix="/api", tags=["projects"])
service = ProjectService()


@router.get("/projects")
def list_projects():
    return service.list_projects()


@router.post("/projects", status_code=201)
def create_project(body: CreateProject):
    return service.create_project(body)


@router.get("/projects/{project_id}")
def get_project(project_id: str):
    return service.get_project(project_id)


@router.patch("/projects/{project_id}")
def edit_project(project_id: str, body: EditProject):
    return service.edit_project(project_id, body)


@router.delete("/projects/{project_id}")
def delete_project(project_id: str):
    return service.delete_project(project_id)


@router.get("/projects/{project_id}/settings")
def get_project_settings(project_id: str):
    return service.get_project_settings(project_id)


@router.put("/projects/{project_id}/settings")
def update_project_settings(project_id: str, body: ProjectPolicy):
    return service.update_project_settings(project_id, body)
