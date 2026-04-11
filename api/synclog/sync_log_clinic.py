import asyncio
import inspect
import json
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from redis.exceptions import RedisError
from sqlalchemy.orm import Session

from auth.oauth2 import get_current_user
from core.database import get_db
from core.models import SyncStatus, Users
from core.queue import async_redis
from core.schemas import sync_log_detail_out, sync_log_page_out
from infra.rbac import require_clinic_access, require_dso_access
from infra.sync_log_events import clinic_sync_logs_channel, dso_sync_logs_channel
from infra.sync_log_service import (
    build_clinic_page_snapshot,
    build_clinic_sync_log_detail,
    build_clinic_page_snapshot_cached
)


router = APIRouter(prefix = "/clinics",  tags= ["Sync Logs"])

@router.get("/{clinic_id}/sync-logs", response_model=sync_log_page_out)
async def get_clinic_sync_logs_page(
    clinic_id: UUID,
    status: SyncStatus | None = Query(default=None),
    search: str | None = Query(default=None, max_length=100),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_clinic_access(db=db, user_id=current_user.id, clinic_id=clinic_id)

    return build_clinic_page_snapshot_cached(
        db,
        clinic_id=clinic_id,
        status=status,
        search=search,
        limit=limit,
        date_from=date_from,
        date_to=date_to,
        cursor=cursor,
    )


@router.get("/{clinic_id}/sync-logs/stream")
async def stream_clinic_sync_logs_events(
    clinic_id: UUID,
    request: Request,
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_clinic_access(db=db, user_id=current_user.id, clinic_id=clinic_id)
    channel = clinic_sync_logs_channel(clinic_id)

    async def event_stream():
        pubsub = async_redis.pubsub()
        await pubsub.subscribe(channel)

        try:
            yield 'event: connected\ndata: {"type":"connected"}\n\n'

            while True:
                if await request.is_disconnected():
                    break

                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=15.0,
                )

                if message and message.get("type") == "message":
                    yield f"event: sync_logs_changed\ndata: {message['data']}\n\n"
                else:
                    yield ": keep-alive\n\n"

                await asyncio.sleep(0.1)

        except RedisError:
            yield 'event: stream_error\ndata: {"type":"stream_error"}\n\n'

        finally:
            await pubsub.unsubscribe(channel)
            close_method = getattr(pubsub, "aclose", None) or getattr(pubsub, "close")
            close_result = close_method()
            if inspect.isawaitable(close_result):
                await close_result

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{clinic_id}/sync-logs/{sync_log_id}", response_model=sync_log_detail_out)
async def get_clinic_sync_log_detail(
    clinic_id: UUID,
    sync_log_id: UUID,
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_clinic_access(db=db, user_id=current_user.id, clinic_id=clinic_id)

    return build_clinic_sync_log_detail(
        db,
        clinic_id=clinic_id,
        sync_log_id=sync_log_id,
    )



