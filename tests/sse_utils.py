from __future__ import annotations

import json
import re
from typing import Any

SSE_EVENT_PATTERN = re.compile(r"^event: (.+)\ndata: (.+)$", re.MULTILINE)


def parse_sse_events(body: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for match in SSE_EVENT_PATTERN.finditer(body.strip()):
        event_name = match.group(1)
        data = json.loads(match.group(2))
        events.append((event_name, data))
    return events
