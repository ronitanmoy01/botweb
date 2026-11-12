import hashlib
import re
from datetime import datetime, timezone


def is_bot(user_agent: str) -> bool:
    """Check if user agent looks like a bot/crawler."""
    bot_patterns = r"bot|crawler|spider|scraper|curl|wget|monitoring|checker|preview|validator"
    return bool(re.search(bot_patterns, user_agent.lower()))


def visitor_hash(ip: str, user_agent: str, salt: str) -> str:
    """
    Daily-rotated visitor identifier. Never stores raw IP.
    Salt rotates daily (YYYYMMDD + secret_key) so hashes don't persist across days.
    """
    return hashlib.sha256(f"{ip}:{user_agent}:{salt}".encode()).hexdigest()[:64]


def today_salt(secret_key: str) -> str:
    """Salt that rotates daily."""
    return datetime.now(timezone.utc).strftime("%Y%m%d") + secret_key
