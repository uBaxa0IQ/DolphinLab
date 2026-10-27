"""Internal-only execution service. Generated code runs as nobody, without platform secrets."""

import hmac
import importlib.metadata
import json
import os
import subprocess
import sys
from pathlib import Path

import psutil
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

ROOT = Path(os.environ.get("RUNS_ROOT", "/data/runs")).resolve()
TOKEN = os.environ["EXECUTOR_TOKEN"]
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def authorized(x_executor_token: str = Header(default="")):
    if not hmac.compare_digest(x_executor_token, TOKEN):
        raise HTTPException(401)


class JobRef(BaseModel):
    manifest: str
    token: str


def manifest(ref):
    path = Path(ref.manifest).resolve()
    if not path.is_relative_to(ROOT) or path.name != "job.json" or not path.is_file():
        raise HTTPException(404, "Unknown job manifest")
    job = json.loads(path.read_text())
    if (
        not hmac.compare_digest(job.get("token", ""), ref.token)
        or Path(job["folder"]).resolve() != path.parent
    ):
        raise HTTPException(409, "Job identity mismatch")
    if not Path(job["script"]).resolve().is_relative_to(path.parent / "code"):
        raise HTTPException(400, "Source must be inside the frozen job code directory")
    return path, job


def inspect(path, job):
    result = path.parent / "result.json"
    if result.exists():
        data = json.loads(result.read_text())
        if data.get("token") == job["token"] and data.get("job_id") == job["id"]:
            return {"state": data["state"], "result": data}
    identity_path = path.parent / "process.json"
    if identity_path.exists():
        identity = json.loads(identity_path.read_text())
        try:
            process = psutil.Process(identity["pid"])
            if (
                identity.get("token") == job["token"]
                and abs(process.create_time() - identity["created"]) < 0.05
                and str(path) in process.cmdline()
            ):
                return {"state": "running"}
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    intent = path.parent / "launch.intent"
    if intent.exists():
        import time

        return {
            "state": "launching" if time.time() - intent.stat().st_mtime < 10 else "interrupted"
        }
    return {"state": "queued"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/capabilities", dependencies=[Depends(authorized)])
def capabilities():
    packages = [
        "numpy",
        "scipy",
        "pandas",
        "polars",
        "pyarrow",
        "scikit-learn",
        "catboost",
        "lightgbm",
        "xgboost",
        "optuna",
        "category-encoders",
        "matplotlib",
        "seaborn",
        "joblib",
    ]
    return {
        name: importlib.metadata.version("xgboost-cpu" if name == "xgboost" else name)
        for name in packages
    }


@app.post("/inspect", dependencies=[Depends(authorized)])
def inspect_job(ref: JobRef):
    path, job = manifest(ref)
    return inspect(path, job)


@app.post("/execute", dependencies=[Depends(authorized)])
def execute(ref: JobRef):
    path, job = manifest(ref)
    current = inspect(path, job)
    if current["state"] != "queued":
        return current
    try:
        descriptor = os.open(
            path.parent / "launch.intent", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        os.close(descriptor)
    except FileExistsError:
        return inspect(path, job)
    # Only the supervisor gets the job's resource/deadline manifest. No DB/model configuration.
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(path.parent),
        "TMPDIR": str(path.parent / "tmp"),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
        "TASK_ROOT": str(path.parents[3] / "task"),
        "JOB_DIR": str(path.parent),
        "RESEARCH_CPU": str(job["cpus"]),
    }
    env.update(
        {
            key: str(job["cpus"])
            for key in (
                "OMP_NUM_THREADS",
                "OPENBLAS_NUM_THREADS",
                "MKL_NUM_THREADS",
                "NUMEXPR_NUM_THREADS",
            )
        }
    )
    subprocess.Popen(
        [sys.executable, "-u", str(Path(__file__).with_name("supervisor.py")), str(path)],
        cwd=path.parent,
        env=env,
        user=65534,
        group=1000,
        extra_groups=[],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return {"state": "launching"}


@app.post("/cancel", dependencies=[Depends(authorized)])
def cancel(ref: JobRef):
    path, job = manifest(ref)
    (path.parent / "cancel.request").touch()
    return {"cancellation_requested": True}
