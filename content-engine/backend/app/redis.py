"""
app/redis.py — Redis client initialization and utility functions.
"""
import redis
from app.config import settings
from app.logger import get_logger

log = get_logger(__name__)

# Use the redis_url from settings. 
# decode_responses=True ensures we get strings back instead of bytes.
redis_client = redis.from_url(
    settings.redis_url, 
    decode_responses=True,
    socket_timeout=5,
    socket_connect_timeout=5,
    retry_on_timeout=True
)

def get_redis_client():
    """
    Returns the global redis client instance.
    """
    return redis_client

def check_redis_health():
    """
    Checks if redis is alive.
    """
    try:
        return redis_client.ping()
    except redis.exceptions.RedisError as exc:
        log.warning("redis.health_check.failed", error=str(exc))
        return False
