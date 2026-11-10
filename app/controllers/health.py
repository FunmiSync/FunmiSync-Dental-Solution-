import asyncio
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from app.infrastructure.database import ping_db
from app.infrastructure.queue import async_redis

router = APIRouter(tags=["Health"])


@router.get("/health/ready")
async def readiness():
    database_ok, _ = await run_in_threadpool(ping_db, check_schema=True)
    try:
        redis_ok = bool(await asyncio.wait_for(async_redis.ping(), timeout=3))
    except Exception:
        redis_ok = False
    ready = database_ok and redis_ok
    return JSONResponse(status_code=200 if ready else 503,
                        content={"status": "ready" if ready else "unavailable",
                                 "database": database_ok, "redis": redis_ok})
