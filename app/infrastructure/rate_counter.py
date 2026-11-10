"""Keep increment and expiry atomic, including repair of old immortal keys."""
COUNTER_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if redis.call('TTL', KEYS[1]) < 0 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return count
"""


async def increment_window(redis, key, seconds):
    return int(await redis.eval(COUNTER_SCRIPT, 1, key, seconds))
