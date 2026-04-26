"""Daily cache for API calls — avoids slow requests on page load."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).parent.parent / "data"
CACHE_FILE = CACHE_DIR / "plook_cache.json"


def _load_cache() -> dict:
    """Load cache from disk."""
    if not CACHE_FILE.exists():
        return {}
    try:
        return json.loads(CACHE_FILE.read_text())
    except Exception:
        return {}


def _save_cache(data: dict):
    """Save cache to disk."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False))


def get_cached(key: str) -> list | None:
    """Get cached data for today. Returns None if stale or missing."""
    cache = _load_cache()
    entry = cache.get(key)
    if not entry:
        return None
    cached_date = entry.get("date")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if cached_date != today:
        return None
    return entry.get("data", [])


def set_cached(key: str, data: list):
    """Cache data with today's date."""
    cache = _load_cache()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cache[key] = {"date": today, "data": data}
    _save_cache(cache)
