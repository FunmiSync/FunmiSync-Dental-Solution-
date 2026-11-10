from datetime import date
from functools import partial
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.controllers.sync_logs.streaming import require_stream_token, sync_log_event_response
from app.infrastructure.database import get_db
from app.models.entities import SyncStatus, Users
from app.schemas.api import sync_log_detail_out, sync_log_page_out
from app.security.oauth2 import get_current_user
from app.security.rbac import require_dso_access
from app.security.stream_access import revalidate_stream
from app.services.sync_log_events import dso_sync_logs_channel
from app.services.sync_log_service import build_dso_page_snapshot_cached, build_sync_log_detail

router = APIRouter(prefix="/dsos", tags=["Sync Logs"])


@router.get("/{dso_id}/sync-logs", response_model= sync_log_page_out)
async def get_dso_syn_logs_page(
    dso_id: UUID,
    clinic_id: UUID | None = Query(default=None),
    status: SyncStatus | None = Query(default=None),
    search: str | None = Query(default=None, max_length=100),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: Users = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    await require_dso_access(db=db, user_id=current_user.id, dso_id=dso_id)
    return await build_dso_page_snapshot_cached(
        db,
        dso_id=dso_id,
        clinic_id=clinic_id,
        status=status,
        search=search,
        limit=limit,
        date_from=date_from,
        date_to=date_to,
        cursor=cursor,
    )


@router.get("/{dso_id}/sync-logs/stream")
async def stream_dso_sync_logs_page(
    dso_id: UUID,
    request: Request,
):
    token = require_stream_token(request)
    check_access = partial(revalidate_stream, token, dso_id=dso_id)
    await check_access()
    return sync_log_event_response(request, channel=dso_sync_logs_channel(dso_id), revalidate=check_access)


@router.get("/{dso_id}/sync-logs/{sync_log_id}", response_model=sync_log_detail_out)
async def get_dso_sync_log_detail(
    dso_id: UUID,
    sync_log_id: UUID,
    current_user: Users = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await require_dso_access(db=db, user_id=current_user.id, dso_id=dso_id)
    return await build_sync_log_detail(db, dso_id=dso_id, sync_log_id=sync_log_id)
