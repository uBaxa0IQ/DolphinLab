"""Shared request guards."""

import os

from fastapi import HTTPException

from dolphinlab.infrastructure.database import Project


def require_project(db, project_id):
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "Проект не найден")
    return project


def execution_enabled():
    return os.getenv("ENABLE_MODEL_CALLS", "false").lower() == "true"
