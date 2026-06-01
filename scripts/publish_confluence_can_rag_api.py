#!/usr/bin/env python3
"""将 docs/confluence/frontend-api-integration.md 同步到 Confluence CAN-RAG API 页面。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PAGE_ID = "66469"
CLOUD_ID = "308a505a-b882-42ae-b713-2965b58d6c7e"
VERSION_MESSAGE = "§8.0 片段存储格式、§8.2 sources、§9.1 markdown 字段（2026-05-31）"
DOC_PATH = Path(__file__).resolve().parents[1] / "docs/confluence/frontend-api-integration.md"


def main() -> int:
    body = DOC_PATH.read_text(encoding="utf-8")
    payload = {
        "cloudId": CLOUD_ID,
        "pageId": PAGE_ID,
        "contentFormat": "markdown",
        "versionMessage": VERSION_MESSAGE,
        "body": body,
    }
    # 输出 JSON 供外部 MCP 工具消费；也可由 CI 调用 Atlassian API。
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
