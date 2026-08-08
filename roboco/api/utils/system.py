"""
System Route Helpers

Pure, side-effect-free helpers backing roboco/api/routes/system.py.
"""

from datetime import datetime, timedelta


def _resume_at(hit_at: str | None, retry_after: float | None) -> str | None:
    """Estimated lift time = hit_at + retry_after, ISO; falls back to hit_at."""
    if not hit_at or retry_after is None:
        return hit_at
    try:
        lifted = datetime.fromisoformat(hit_at) + timedelta(seconds=retry_after)
    except (ValueError, TypeError):
        return hit_at
    return lifted.isoformat()
