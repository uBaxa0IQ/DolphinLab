"""Versioned goals and research iteration history."""

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from dolphinlab.api.schemas import CreateGoal, CreateIteration
from dolphinlab.application.knowledge import KnowledgeService
from dolphinlab.application.research import ResearchService
from dolphinlab.application.results import ResultsService
from dolphinlab.infrastructure.database import PMAction, Session

router = APIRouter(prefix="/api", tags=["research"])
service = ResearchService()
knowledge = KnowledgeService()
results = ResultsService()


@router.get("/projects/{project_id}/runs/{run_id}/results")
def result_index(project_id: str, run_id: str):
    return results.index(project_id, run_id)


@router.get("/projects/{project_id}/runs/{run_id}/results/{result_id}")
def result_detail(project_id: str, run_id: str, result_id: str):
    return results.result(project_id, run_id, result_id)


@router.get("/projects/{project_id}/runs/{run_id}/evidence/{artifact_id}")
def result_artifact(project_id: str, run_id: str, artifact_id: str):
    return results.artifact(project_id, run_id, artifact_id)


@router.get("/projects/{project_id}/memory")
def list_memory(project_id: str, include_stale: bool = False, offset: int = Query(default=0, ge=0)):
    return knowledge.list_memory(project_id, include_stale=include_stale, offset=offset)


@router.get("/projects/{project_id}/messages/{message_id}/actions/{action_id}")
def read_action(project_id: str, message_id: str, action_id: str):
    with Session() as db:
        action = db.scalar(
            select(PMAction).where(
                PMAction.project_id == project_id,
                PMAction.message_id == message_id,
                PMAction.id == action_id,
            )
        )
        if not action:
            raise HTTPException(404, "Расчёт не найден в этом проекте")
        return {
            "id": action.id,
            "kind": action.kind,
            "result": action.result,
            "arguments": action.arguments,
            "created": action.created.isoformat(),
        }


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
