from fastapi import APIRouter

from dolphinlab.api.schemas import UpdateSettings
from dolphinlab.application.settings import SettingsService

router = APIRouter(prefix="/api", tags=["settings"])
service = SettingsService()


@router.get("/settings")
def get_settings():
    return service.get_settings()


@router.put("/settings")
def update_settings(body: UpdateSettings):
    return service.update_settings(body)
