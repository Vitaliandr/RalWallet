import time

from redis.asyncio import Redis

from ralwallet.settings import settings

_redis: Redis | None = None


def get_redis() -> Redis:
    global _redis
    if _redis is None:
        _redis = Redis.from_url(settings.redis_url, decode_responses=True)
    return _redis


async def hit(redis: Redis, key: str, limit: int, window: int = 60) -> bool:
    # fixed window, False если превышен
    bucket = int(time.time()) // window
    redis_key = f"rl:{key}:{bucket}"

    #incr + expire одним запросом
    async with redis.pipeline(transaction=True) as pipe:
        pipe.incr(redis_key)
        pipe.expire(redis_key, window)
        count, _ = await pipe.execute()

    return count <= limit


#---- подбор пароля ----
TRIES = 5
LOCK = 15 * 60  #сек


# incr ДО проверки пароля, иначе параллельные запросы проскакивают
#вернёт сколько ждать, 0 = можно
async def attempt(redis: Redis, key: str) -> int:
    redis_key = f"tries:{key}"
    async with redis.pipeline(transaction=True) as pipe:
        pipe.incr(redis_key)
        pipe.expire(redis_key, LOCK, nx=True)  # окно от первой попытки
        count, _ = await pipe.execute()
    if count > TRIES:
        return max(await redis.ttl(redis_key), 1)
    return 0


async def reset_attempts(redis: Redis, key: str) -> None:
    await redis.delete(f"tries:{key}")
