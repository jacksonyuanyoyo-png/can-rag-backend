#!/usr/bin/env python3
"""Remove empty PG knowledge-base stub rows created by presign/import tests."""

from __future__ import annotations

import os
import sys

import psycopg

from app.core.database import normalize_psycopg_url

KEEP_KB_IDS = frozenset(
    {
        "adb8c318-a14f-4de5-9016-bfab7955caa0",
        "c4f6b63b-1c78-4043-926d-8b258f0e7fcf",
    }
)

DELETE_SQL = """
DELETE FROM app.t_dim_knowledge_base kb
WHERE kb.id <> ALL(%(keep_ids)s)
  AND NOT EXISTS (SELECT 1 FROM app.t_dim_kb_file f WHERE f.kb_id = kb.id)
  AND NOT EXISTS (SELECT 1 FROM app.t_fact_upload_object u WHERE u.kb_id = kb.id)
  AND NOT EXISTS (SELECT 1 FROM app.t_fact_import_job j WHERE j.kb_id = kb.id)
  AND NOT EXISTS (SELECT 1 FROM app.t_fact_kb_index i WHERE i.kb_id = kb.id)
RETURNING kb.id, kb.name
"""


def main() -> int:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        print("DATABASE_URL is required", file=sys.stderr)
        return 1

    dsn = normalize_psycopg_url(database_url)
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(DELETE_SQL, {"keep_ids": list(KEEP_KB_IDS)})
            deleted = cur.fetchall()
        conn.commit()

    print(f"Deleted {len(deleted)} orphan knowledge-base stub(s).")
    for kb_id, name in deleted[:20]:
        print(f"  - {kb_id} ({name})")
    if len(deleted) > 20:
        print(f"  ... and {len(deleted) - 20} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
