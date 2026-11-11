import inspect
from redis.exceptions import RedisError


async def close_pubsub(pubsub, channel):
    try:
        await pubsub.unsubscribe(channel)
    except RedisError:
        pass
    finally:
        close = getattr(pubsub, "aclose", None) or getattr(pubsub, "close")
        result = close()
        if inspect.isawaitable(result):
            await result
