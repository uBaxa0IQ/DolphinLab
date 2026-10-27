"""Explicit historical evidence selection; it never imports an old scientific verdict."""

import os
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import select

from dolphinlab.infrastructure.database import ResearchObject, Run
from dolphinlab.infrastructure.store import PostgresStore
from dolphinlab.research.results import ResultRegistry


def validate_prior_results(db, project_id, references):
    seen = set()
    for ref in references:
        key = (str(ref.run_id), ref.result_id)
        if key in seen:
            raise HTTPException(422, "Результат выбран повторно")
        seen.add(key)
        run = db.scalar(select(Run).where(Run.project_id == project_id, Run.id == key[0]))
        result = (
            db.scalar(
                select(ResearchObject.data).where(
                    ResearchObject.run_id == key[0],
                    ResearchObject.kind == "result_revision",
                    ResearchObject.object_id == ref.result_id,
                )
            )
            if run
            else None
        )
        if not result or result["sha256"] != ref.sha256:
            raise HTTPException(
                422, "Ссылка на прежний результат или его хеш недействительна в проекте"
            )
        if run.status not in {"completed", "failed", "stopped"}:
            raise HTTPException(409, "Дождись завершения запуска с выбранным результатом")


def registry_for(run_id):
    root = (Path(os.getenv("RUNS_ROOT", "/data/runs")) / run_id).resolve()
    store = PostgresStore(run_id, root / "runtime")
    return ResultRegistry(SimpleNamespace(store=store)), root


def result_index(run_id, limit=10):
    registry, _ = registry_for(run_id)
    return [
        {**registry.summary(item), "status": registry.status(item)}
        for item in registry.store.all("result_revision")[-limit:]
    ]


def selected_results(db, project_id, references, inputs):
    validate_prior_results(db, project_id, references)
    selected = []
    for ref in references:
        registry, root = registry_for(str(ref.run_id))
        item = registry.get(ref.result_id)
        artifact = registry.store.get("artifact", item["artifact_id"])
        path = Path(artifact["path"]).resolve()
        if not path.is_relative_to(root) or not registry.artifact_valid(artifact["id"]):
            raise HTTPException(409, "Выбранное историческое доказательство повреждено")
        selected.append(
            {
                **registry.summary(item),
                "status": registry.status(item),
                "run_id": str(ref.run_id),
                "reason": ref.reason,
                "artifact_path": str(path),
                "input_versions": item["input_versions"],
                "applicable_to_inputs": all(i["id"] in inputs for i in item["input_versions"]),
                "assessment": registry.latest_assessment(item["id"]),
                "notice": "Historical evidence only; check definitions and scope before reuse. No new-run approval is granted.",
            }
        )
    return selected
