"""Server-Sent Events responses for live sync-log updates.

Browsers cannot send an Authorization header with EventSource, so the stream
token comes from a cookie or header and is rechecked before every event.
"""
import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable

from fastapi import HTTPException, Request, status
from fastapi.responses import StreamingResponse
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from app.infrastructure.queue import async_redis
from app.infrastructure.streams import close_pubsub
from app.security.oauth2 import get_stream_token_from_request

logger = logging.getLogger(__name__)

WAIT_SECONDS = 15.0
THROTTLE_SECONDS = 0.1
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",  # stop nginx from buffering the stream
}


def require_stream_token(request: Request) -> str:
    token = get_stream_token_from_request(request)
    if not token:
        logger.warning("Sync log stream rejected: missing stream token", extra={"path": request.url.path})
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing stream token")
    return token


def sync_log_event_response(request: Request, *, channel: str, revalidate: Callable[[], Awaitable[None]]) -> StreamingResponse:
    return StreamingResponse(
        _sync_log_events(request, channel=channel, revalidate=revalidate),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


async def _sync_log_events(request: Request, *, channel: str, revalidate: Callable[[], Awaitable[None]]) -> AsyncIterator[str]:
    pubsub = async_redis.pubsub()
    try:
        await pubsub.subscribe(channel)
        yield 'event: connected\ndata: {"type":"connected"}\n\n'

        while not await request.is_disconnected():
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=WAIT_SECONDS)

            # Recheck the token and membership after waiting, before delivering anything,
            # so a revoked user never receives another event.
            await revalidate()

            if message and message.get("type") == "message":
                yield f"event: sync_logs_changed\ndata: {message['data']}\n\n"
            else:
                yield ": keep-alive\n\n"

            await asyncio.sleep(THROTTLE_SECONDS)

    except HTTPException:
        yield 'event: stream_error\ndata: {"type":"access_revoked"}\n\n'
    except (RedisError, SQLAlchemyError):
        yield 'event: stream_error\ndata: {"type":"stream_error"}\n\n'
    finally:
        await close_pubsub(pubsub, channel)
