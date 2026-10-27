from fastapi import APIRouter

from dolphinlab.api.schemas import ControlRun, StartRun, UpdateRunBudget
from dolphinlab.application.runs import RunService

router = APIRouter(prefix="/api", tags=["runs"])
service = RunService()


@router.post("/projects/{project_id}/runs")
def start_run(project_id: str, body: StartRun | None = None):
    return service.start_run(project_id, body)


@router.get("/projects/{project_id}/runs")
def list_runs(project_id: str):
    return service.list_runs(project_id)


@router.put("/projects/{project_id}/runs/{run_id}/budget")
def update_run_budget(project_id: str, run_id: str, body: UpdateRunBudget):
    return service.update_budget(project_id, run_id, body)


@router.post("/projects/{project_id}/runs/{run_id}/control")
def control_run(project_id: str, run_id: str, body: ControlRun):
    return service.control_run(project_id, run_id, body)


@router.get("/projects/{project_id}/runs/{run_id}/events")
def research_events(project_id: str, run_id: str, after: int = 0):
    return service.research_events(project_id, run_id, after)


@router.get("/projects/{project_id}/runs/{run_id}/artifacts")
def run_artifacts(project_id: str, run_id: str):
    return service.run_artifacts(project_id, run_id)


@router.get("/projects/{project_id}/runs/{run_id}/artifact")
def run_artifact(project_id: str, run_id: str, path: str):
    return service.run_artifact(project_id, run_id, path)
