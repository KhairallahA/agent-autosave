"""Turn each agent's hook payloads into ledger events."""
from __future__ import annotations

from typing import Any

ID_MAX = 200


def vendor_id(payload: dict[str, Any], key: str) -> dict[str, str]:
    """{"id": ...} from the agent's own identifier for this event, or {}.

    Only identifiers the agent itself sends (recorded in the fixtures) are
    used, never one derived from content or time: two identical commands run
    twice are two actions, one hook delivered twice is one."""
    value = payload.get(key)
    if isinstance(value, str) and 0 < len(value) <= ID_MAX:
        return {"id": value}
    return {}
