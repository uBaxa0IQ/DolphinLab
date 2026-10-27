import hashlib
import re
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from fastapi.responses import FileResponse

from sonder.api.dependencies import require_project
from sonder.api.presenters import file_view
from sonder.infrastructure.database import DATA_ROOT, Event, InputFile

from .base import Service

MAX_FILE_BYTES = 100 * 1024 * 1024
ALLOWED_EXTENSIONS = {
    ".md",
    ".txt",
    ".csv",
    ".tsv",
    ".parquet",
    ".json",
    ".jsonl",
    ".xlsx",
    ".zip",
    ".gz",
    ".npy",
    ".npz",
    ".feather",
}


class FileService(Service):
    async def upload_file(self, project_id: str, file: UploadFile):
        with self.sessions() as db:
            require_project(db, project_id)
        name = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
        if (
            not name
            or len(name) > 240
            or re.search(r"[\x00-\x1f\x7f]", name)
            or Path(name).suffix.lower() not in ALLOWED_EXTENSIONS
        ):
            await file.close()
            raise HTTPException(400, "Допустимы файлы данных, task.md и ZIP; имя до 240 символов")
        file_id = str(uuid4())
        folder = DATA_ROOT / "projects" / project_id / "inputs"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / file_id
        total = 0
        digest = hashlib.sha256()
        try:
            # Incoming names are metadata only; they never select a filesystem path.
            with path.open("xb") as target:
                while chunk := await file.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_FILE_BYTES:
                        raise HTTPException(413, "Файл больше 100 МБ")
                    target.write(chunk)
                    digest.update(chunk)
            if total == 0:
                raise HTTPException(400, "Файл пустой")
            with self.sessions.begin() as db:
                require_project(db, project_id)
                record = InputFile(
                    id=file_id,
                    project_id=project_id,
                    name=name,
                    stored_name=file_id,
                    size=total,
                    sha256=digest.hexdigest(),
                )
                db.add(record)
                db.add(
                    Event(
                        project_id=project_id,
                        kind="file_uploaded",
                        payload={"file_id": file_id, "name": name},
                    )
                )
                result = file_view(record)
            return result
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        finally:
            await file.close()

    def download_file(self, project_id: str, file_id: str):
        with self.sessions() as db:
            require_project(db, project_id)
            file = db.get(InputFile, file_id)
            if file is None or file.project_id != project_id:
                raise HTTPException(404, "Файл не найден")
            path = DATA_ROOT / "projects" / project_id / "inputs" / file.stored_name
            if not path.is_file():
                raise HTTPException(404, "Файл отсутствует в хранилище")
            return FileResponse(
                path,
                filename=file.name,
                media_type="application/octet-stream",
                headers={"X-Content-Type-Options": "nosniff"},
            )
