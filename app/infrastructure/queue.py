from redis import Redis
from redis.asyncio import Redis as AsyncRedis
from rq import Queue
from app.config import settings



redis_client = Redis.from_url(
    settings.redis_url,
    decode_responses=True,
    socket_connect_timeout=2,
    socket_timeout=2,
)
async_redis = AsyncRedis.from_url(
    settings.redis_url,
    decode_responses=True,
    socket_connect_timeout=2,
    socket_timeout=2,
)

# create a queue
queue_redis = Redis.from_url(settings.redis_url, decode_responses=False,
                             socket_connect_timeout=2, socket_timeout=2)
appointments_queue = Queue("appointments", connection=queue_redis)




