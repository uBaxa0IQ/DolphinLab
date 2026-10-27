"""Versioned goals and research iteration history."""

from fastapi import APIRouter

from dolphinlab.api.schemas import CreateGoal, CreateIteration
from dolphinlab.application.research import ResearchService

router = APIRouter(prefix="/api", tags=["research"])
service = ResearchService()


@router.get("/projects/{project_id}/goals")
def list_goals(project_id: str):
    return service.list_goals(project_id)


@router.post("/projects/{project_id}/goals", status_code=201)
def create_goal(project_id: str, body: CreateGoal):
    return service.create_goal(project_id, body)


@router.get("/projects/{project_id}/iterations")
def list_iterations(project_id: str):
    return service.list_iterations(project_id)


@router.get("/projects/{project_id}/iterations/{iteration_id}")
def get_iteration(project_id: str, iteration_id: str):
    return service.get_iteration(project_id, iteration_id)


@router.post("/projects/{project_id}/iterations", status_code=201)
def create_iteration(project_id: str, body: CreateIteration):
    return service.create_iteration(project_id, body)
