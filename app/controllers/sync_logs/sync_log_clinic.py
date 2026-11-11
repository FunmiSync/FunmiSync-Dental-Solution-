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
from app.security.rbac import require_clinic_access
from app.security.stream_access import revalidate_stream
from app.services.sync_log_events import clinic_sync_logs_channel
from app.services.sync_log_service import build_clinic_page_snapshot_cached, build_clinic_sync_log_detail

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
    db: AsyncSession = Depends(get_db),
):
    await require_clinic_access(db=db, user_id=current_user.id, clinic_id=clinic_id)
    return await build_clinic_page_snapshot_cached(
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
):
    token = require_stream_token(request)
    check_access = partial(revalidate_stream, token, clinic_id=clinic_id)
    await check_access()
    return sync_log_event_response(request, channel=clinic_sync_logs_channel(clinic_id), revalidate=check_access)


@router.get("/{clinic_id}/sync-logs/{sync_log_id}", response_model=sync_log_detail_out)
async def get_clinic_sync_log_detail(
    clinic_id: UUID,
    sync_log_id: UUID,
    current_user: Users = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await require_clinic_access(db=db, user_id=current_user.id, clinic_id=clinic_id)
    return await build_clinic_sync_log_detail(db, clinic_id=clinic_id, sync_log_id=sync_log_id)
