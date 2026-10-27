from fastapi import APIRouter, File, UploadFile

from dolphinlab.application.files import FileService

router = APIRouter(prefix="/api", tags=["files"])
service = FileService()


@router.post("/projects/{project_id}/files", status_code=201)
async def upload_file(project_id: str, file: UploadFile = File(...)):
    return await service.upload_file(project_id, file)


@router.get("/projects/{project_id}/files/{file_id}")
def download_file(project_id: str, file_id: str):
    return service.download_file(project_id, file_id)
