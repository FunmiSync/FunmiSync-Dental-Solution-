from redis import Redis
from redis.asyncio import Redis as AsyncRedis
from rq import Queue

from app.config import settings

# Application Redis (caches, rate limits, pub/sub) is async.
async_redis = AsyncRedis.from_url(
    settings.redis_url,
    decode_responses=True,
    socket_connect_timeout=2,
    socket_timeout=2,
)

# RQ only supports a synchronous, binary Redis client; enqueue from async code
# through run_in_threadpool so it never blocks the event loop.
queue_redis = Redis.from_url(settings.redis_url, decode_responses=False,
                             socket_connect_timeout=2, socket_timeout=2)
appointments_queue = Queue("appointments", connection=queue_redis)
