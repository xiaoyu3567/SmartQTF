from datetime import datetime, timezone
from typing import Any, Dict


def format_timestamp_ymdhm(timestamp: int, tz=timezone.utc) -> str:
    """Format a unix timestamp as yyyy-m-d h:mm for human-facing payloads."""
    dt = datetime.fromtimestamp(int(timestamp), tz=tz)
    return f"{dt.year}-{dt.month}-{dt.day} {dt.hour}:{dt.minute:02d}"


def add_display_times(payload: Any) -> Any:
    """Recursively add display time fields next to timestamp-like integer fields.

    Canonical machine fields stay unchanged. This helper only enriches display
    payloads, for example:
    - timestamp -> time
    - as_of_timestamp -> as_of_time
    - source_window_start -> source_window_start_time
    - source_window_end -> source_window_end_time
    - window_start_timestamp -> window_start_time
    - window_end_timestamp -> window_end_time
    """
    if isinstance(payload, list):
        return [add_display_times(item) for item in payload]
    if not isinstance(payload, dict):
        return payload

    enriched: Dict[str, Any] = {}
    for key, value in payload.items():
        enriched[key] = add_display_times(value)
        display_key = _display_time_key(key)
        if display_key and display_key not in payload and isinstance(value, int) and value >= 0:
            enriched[display_key] = format_timestamp_ymdhm(value)
    return enriched


def _display_time_key(key: str) -> str:
    if key == "timestamp":
        return "time"
    if key.endswith("_timestamp"):
        return f"{key[:-10]}_time"
    if key in {"source_window_start", "window_start"}:
        return f"{key}_time"
    if key in {"source_window_end", "window_end"}:
        return f"{key}_time"
    return ""
