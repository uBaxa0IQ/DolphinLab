"""Project-scoped read API for analytical revisions and historical report pages."""

import os
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select

from dolphinlab.application.knowledge import latest_inputs
from dolphinlab.infrastructure.database import Run
from dolphinlab.infrastructure.store import PostgresStore
from dolphinlab.research.results import ResultRegistry

from .base import Service


class ResultsService(Service):
    def reader(self, db, project_id, run_id):
        run = db.scalar(select(Run).where(Run.id == run_id, Run.project_id == project_id))
        if not run:
            raise HTTPException(404, "Запуск не найден в этом проекте")
        root = (Path(os.getenv("RUNS_ROOT", "/data/runs")) / run.id).resolve()
        registry = ResultRegistry(
            SimpleNamespace(
                store=PostgresStore(run.id, root / "runtime"),
                config=SimpleNamespace(goal_spec=run.goal_snapshot),
            )
        )
        return registry, root

    def index(self, project_id, run_id):
        with self.sessions() as db:
            registry, _ = self.reader(db, project_id, run_id)
            legacy = [
                {
                    "id": c["id"],
                    "kind": "ml_candidate",
                    "title": "ML-кандидат",
                    "status": c.get("review_state", "pending"),
                    "sha256": c["sha256"],
                    "job_id": c["job_id"],
                    "author": c["author"],
                    "legacy": True,
                }
                for c in registry.store.all("candidate")
            ][-30:]
            return {
                "results": [registry.summary(r) for r in registry.store.all("result_revision")][
                    -100:
                ]
                + legacy,
                "pages": registry.store.all("page_revision")[-50:],
                "current": registry.current(),
                "contract": registry.contract(),
            }

    def result(self, project_id, run_id, result_id):
        with self.sessions() as db:
            registry, root = self.reader(db, project_id, run_id)
            try:
                result = registry.get(result_id)
                artifact = registry.store.get("artifact", result["artifact_id"])
                if not Path(artifact["path"]).resolve().is_relative_to(root):
                    raise HTTPException(404)
                status = registry.status(result)
            except KeyError:
                raise HTTPException(404, "Результат не найден в этом запуске")
            current = {item.name: (item.id, item.sha256) for item in latest_inputs(db, project_id)}
            applicable = all(
                current.get(f["name"]) == (f["id"], f["sha256"]) for f in result["input_versions"]
            )
            evidence_files = []
            assessment = registry.latest_assessment(result_id)
            for label, job_id in (
                ("Исходный расчёт", result.get("job_id")),
                ("Независимая проверка", (assessment or {}).get("job_id")),
            ):
                if not job_id:
                    continue
                job = registry.store.get("job", job_id)
                for name, candidate in (
                    ("код", job["script"]),
                    ("stdout", str(Path(job["folder"]) / "stdout.log")),
                    ("stderr", str(Path(job["folder"]) / "stderr.log")),
                ):
                    path = Path(candidate).resolve()
                    if path.is_file() and path.is_relative_to(root):
                        evidence_files.append(
                            {"label": label + " · " + name, "path": str(path.relative_to(root))}
                        )
            return {
                **result,
                "status": status,
                "applicable": applicable,
                "artifact": {k: artifact[k] for k in ("id", "sha256", "size", "job_id")},
                "assessment": assessment,
                "evidence_files": evidence_files,
            }

    def artifact(self, project_id, run_id, artifact_id):
        with self.sessions() as db:
            registry, root = self.reader(db, project_id, run_id)
            try:
                artifact = registry.store.get("artifact", artifact_id)
            except KeyError:
                raise HTTPException(404)
            path = Path(artifact["path"]).resolve()
            if (
                not path.is_relative_to(root)
                or not path.is_file()
                or path.stat().st_size != artifact["size"]
            ):
                raise HTTPException(409, "Файл доказательства изменился или недоступен")
            from dolphinlab.common.files import digest

            if digest(path) != artifact["sha256"]:
                raise HTTPException(409, "Хеш доказательства не совпадает")
            return FileResponse(
                path,
                filename=artifact_id + ".json",
                media_type="application/octet-stream",
                headers={"X-Content-Type-Options": "nosniff"},
            )
