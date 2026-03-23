import logging
import asyncio
import inspect
from datetime import  date
from uuid import UUID
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from auth.oauth2 import get_current_user
from auth.security import decode_json_secret
from infra.sync_log_service import build_page_snapshot, build_sync_log_detail
from core.database import get_db
from core.models import  SyncStatus, Users
from core.schemas import (sync_log_page_out, sync_log_detail_out)
from infra.rbac import require_dso_access
from redis.exceptions import RedisError
from core.queue import async_redis
from infra.sync_log_events import dso_sync_logs_channel


router = APIRouter(prefix="/dsos", tags=["Sync Logs"])

logger= logging.getLogger(__name__)

@router.get("/{dso_id}/syn-logs", response_model= sync_log_page_out)
async def get_dso_syn_logs_page(
    dso_id: UUID,
    clinic_id: UUID | None = Query(default=None),
    status: SyncStatus | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=None),
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    require_dso_access(db=db, user_id= current_user.id, dso_id= dso_id)
    return build_page_snapshot(
        db,
        dso_id=dso_id,
        clinic_id=clinic_id,
        status=status,
        limit=limit,
        date_from=date_from,
        date_to=date_to,
        cursor=cursor,
    )


@router.get("/{dso_id}/sync-logs/stream")
async def stream_dso_sync_logs_page(
    dso_id: UUID,
    request: Request,
    current_user: Users =Depends(get_current_user),
    db: Session = Depends(get_db) 
):
    require_dso_access(db=db, user_id=current_user.id, dso_id = dso_id)
    channel = dso_sync_logs_channel(dso_id)

    async def event_stream():
        pubsub = async_redis.pubsub()
        await pubsub.subscribe(channel)

        try:
            yield 'event: connected\ndata: {"type": "connected"}\n\n'

            while True:
                if await request.is_disconnected():
                    break

                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=15.0
                    )
                if message and message.get("type") =="message":
                    yield f"event: sync_logs_chnaged\ndata:  {message['data']}\n\n"
                else:
                    yield ": Keep-alive\n\n"

                await asyncio.sleep(0.2)
        except RedisError:
            yield 'event: stream_error\ndata: {"type": "stream_error"}\n\n'

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


@router.get("/{dso_id}/sync-logs/{sync_log_id}", response_model=sync_log_detail_out)
async def get_dso_sync_log_detail(
    dso_id: UUID,
    sync_log_id: UUID,
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_dso_access(db=db, user_id=current_user.id, dso_id=dso_id)

    return build_sync_log_detail(
        db,
        dso_id=dso_id,
        sync_log_id=sync_log_id,
    )
 