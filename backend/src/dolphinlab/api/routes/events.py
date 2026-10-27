import asyncio
import json

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from dolphinlab.api.dependencies import require_project
from dolphinlab.infrastructure.database import Event, Session

router = APIRouter(prefix="/api", tags=["events"])


def events_after(project_id, cursor):
    with Session() as db:
        return [
            {"id": e.seq, "kind": e.kind, "data": e.payload}
            for e in db.scalars(
                select(Event)
                .where(Event.project_id == project_id, Event.seq > cursor)
                .order_by(Event.seq)
                .limit(100)
            )
        ]


@router.get("/projects/{project_id}/events")
async def stream_events(
    project_id: str,
    request: Request,
    after: int = 0,
    last_event_id: str | None = Header(default=None),
):
    with Session() as db:
        require_project(db, project_id)
    try:
        cursor = max(0, int(last_event_id or after))
    except ValueError:
        raise HTTPException(400, "Некорректный ID события")

    async def stream():
        nonlocal cursor
        ticks = 0
        while not await request.is_disconnected():
            events = await run_in_threadpool(events_after, project_id, cursor)
            for item in events:
                cursor = item["id"]
                yield f"id: {cursor}\nevent: update\ndata: {json.dumps(item, ensure_ascii=False)}\n\n"
            ticks += 1
            if ticks % 15 == 0:
                yield ": keepalive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
