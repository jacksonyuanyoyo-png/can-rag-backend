#!/usr/bin/env python3
"""End-to-end API smoke test against a running CAN-RAG backend."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
LOGIN_EMAIL = "admin@example.com"
LOGIN_PASSWORD = "admin123"
POLL_TIMEOUT_SEC = 60.0
POLL_INTERVAL_SEC = 1.0

SMOKE_TXT_CONTENT = (
    b"Canadian tax-free savings account smoke test content "
    b"for retrieval verification."
)
SMOKE_QUERY = "tax-free savings"

SSE_EVENT_PATTERN = re.compile(r"^event: (.+)\ndata: (.+)$", re.MULTILINE)


@dataclass
class StepResult:
    name: str
    passed: bool
    status_code: int | None = None
    detail: str = ""
    notes: str = ""


@dataclass
class SmokeContext:
    base_url: str
    token: str | None = None
    kb_id: str | None = None
    file_id: str | None = None
    job_id: str | None = None
    data_id: str | None = None
    conversation_id: str | None = None
    results: list[StepResult] = field(default_factory=list)


def _parse_sse_events(body: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for match in SSE_EVENT_PATTERN.finditer(body.strip()):
        events.append((match.group(1), json.loads(match.group(2))))
    return events


def _json_body(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except json.JSONDecodeError:
        return {"_raw": response.text[:500]}
    if isinstance(payload, dict):
        return payload
    return {"_raw": payload}


def _error_message(payload: dict[str, Any]) -> str:
    err = payload.get("error")
    if isinstance(err, dict):
        code = err.get("code", "")
        msg = err.get("message", "")
        return f"{code}: {msg}".strip(": ")
    return str(payload.get("_raw", ""))[:200]


def record(ctx: SmokeContext, result: StepResult) -> None:
    ctx.results.append(result)
    status = "PASS" if result.passed else "FAIL"
    code = result.status_code if result.status_code is not None else "-"
    line = f"[{status}] {result.name} (HTTP {code})"
    if result.detail:
        line += f" | {result.detail}"
    if result.notes:
        line += f" | note: {result.notes}"
    print(line)


def auth_headers(ctx: SmokeContext) -> dict[str, str]:
    if not ctx.token:
        return {}
    return {"Authorization": f"Bearer {ctx.token}"}


def run_smoke(base_url: str) -> int:
    ctx = SmokeContext(base_url=base_url.rstrip("/"))
    client = httpx.Client(base_url=ctx.base_url, timeout=httpx.Timeout(120.0))

    # 1 ping
    try:
        r = client.get("/test/ping")
        payload = _json_body(r)
        ok = r.status_code == 200 and payload.get("status") == "ok"
        record(
            ctx,
            StepResult(
                "1. GET /test/ping",
                ok,
                r.status_code,
                f"status={payload.get('status')}, service={payload.get('service')}",
            ),
        )
    except httpx.HTTPError as exc:
        record(ctx, StepResult("1. GET /test/ping", False, None, str(exc)))

    # 2 postgres
    try:
        r = client.get("/test/postgres")
        payload = _json_body(r)
        ok = r.status_code == 200
        detail = json.dumps({k: payload.get(k) for k in ("status", "database", "error") if k in payload}, ensure_ascii=False)
        if not ok:
            detail = _error_message(payload) or detail
        record(ctx, StepResult("2. GET /test/postgres", ok, r.status_code, detail))
    except httpx.HTTPError as exc:
        record(ctx, StepResult("2. GET /test/postgres", False, None, str(exc)))

    # 3 login
    try:
        r = client.post(
            "/v1/auth/login",
            json={"email": LOGIN_EMAIL, "password": LOGIN_PASSWORD},
        )
        payload = _json_body(r)
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        token = data.get("accessToken")
        ctx.token = token if isinstance(token, str) else None
        ok = r.status_code == 200 and bool(ctx.token)
        detail = f"accessToken={'yes' if ctx.token else 'no'}, expiresIn={data.get('expiresIn')}"
        if not ok:
            detail = _error_message(payload) or detail
        record(ctx, StepResult("3. POST /v1/auth/login", ok, r.status_code, detail))
    except httpx.HTTPError as exc:
        record(ctx, StepResult("3. POST /v1/auth/login", False, None, str(exc)))

    # 4 create KB
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    kb_name = f"smoke-kb-{ts}"
    try:
        r = client.post("/v1/knowledge-bases", json={"name": kb_name, "description": "smoke"})
        payload = _json_body(r)
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        ctx.kb_id = data.get("id") if isinstance(data.get("id"), str) else None
        ok = r.status_code == 201 and bool(ctx.kb_id)
        detail = f"kb_id={ctx.kb_id}, name={data.get('name')}"
        if not ok:
            detail = _error_message(payload) or detail
        record(ctx, StepResult("4. POST /v1/knowledge-bases", ok, r.status_code, detail))
    except httpx.HTTPError as exc:
        record(ctx, StepResult("4. POST /v1/knowledge-bases", False, None, str(exc)))

  # 5 upload presign + PUT + complete
    upload_ok = False
    upload_item: dict[str, Any] = {}
    if ctx.kb_id and ctx.token:
        try:
            file_name = f"smoke-{ts}.txt"
            r = client.post(
                "/v1/uploads/presign",
                headers=auth_headers(ctx),
                json={
                    "knowledgeBaseId": ctx.kb_id,
                    "files": [
                        {
                            "fileName": file_name,
                            "mimeType": "text/plain",
                            "sizeBytes": len(SMOKE_TXT_CONTENT),
                        }
                    ],
                },
            )
            payload = _json_body(r)
            uploads = []
            if isinstance(payload.get("data"), dict):
                uploads = payload["data"].get("uploads") or []
            if r.status_code == 201 and uploads:
                upload_item = uploads[0]
                put_url = upload_item.get("uploadUrl")
                headers_put = upload_item.get("headers") or {}
                put_note = ""
                if isinstance(put_url, str):
                    put_resp = client.put(put_url, content=SMOKE_TXT_CONTENT, headers=headers_put)
                    put_note = f"PUT uploadUrl status={put_resp.status_code}"
                    if put_resp.status_code >= 400:
                        put_note += " (dev upload route may be missing; complete may leave empty placeholder)"
                else:
                    put_note = "no uploadUrl"

                r2 = client.post(
                    f"/v1/uploads/{upload_item['uploadId']}:complete",
                    headers=auth_headers(ctx),
                    json={
                        "fileId": upload_item["fileId"],
                        "storageKey": upload_item["storageKey"],
                        "etag": "smoke-etag",
                    },
                )
                payload2 = _json_body(r2)
                data2 = payload2.get("data") if isinstance(payload2.get("data"), dict) else {}
                ctx.file_id = data2.get("fileId") or upload_item.get("fileId")
                upload_ok = r2.status_code == 200 and data2.get("status") == "uploaded"
                detail = f"file_id={ctx.file_id}; {put_note}"
                if not upload_ok:
                    detail = _error_message(payload2) or detail
                record(
                    ctx,
                    StepResult(
                        "5. POST /v1/uploads presign+complete",
                        upload_ok,
                        r2.status_code,
                        detail,
                        notes=put_note,
                    ),
                )
            else:
                record(
                    ctx,
                    StepResult(
                        "5. POST /v1/uploads presign+complete",
                        False,
                        r.status_code,
                        _error_message(payload) or "presign failed",
                    ),
                )
        except httpx.HTTPError as exc:
            record(ctx, StepResult("5. POST /v1/uploads presign+complete", False, None, str(exc)))
    else:
        record(
            ctx,
            StepResult(
                "5. POST /v1/uploads presign+complete",
                False,
                None,
                "skipped: missing kb_id or token",
            ),
        )

    import_terminal_ok = False
    job_status = ""

    # 6 create import job
    if ctx.kb_id and ctx.file_id and ctx.token:
        try:
            r = client.post(
                f"/v1/knowledge-bases/{ctx.kb_id}/import-jobs",
                headers=auth_headers(ctx),
                json={
                    "fileIds": [ctx.file_id],
                    "chunking": {"strategy": "default"},
                },
            )
            payload = _json_body(r)
            data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            ctx.job_id = data.get("id") if isinstance(data.get("id"), str) else None
            ok = r.status_code == 201 and bool(ctx.job_id)
            detail = f"job_id={ctx.job_id}, status={data.get('status')}"
            if not ok:
                detail = _error_message(payload) or detail
            record(ctx, StepResult("6. POST import-jobs", ok, r.status_code, detail))
        except httpx.HTTPError as exc:
            record(ctx, StepResult("6. POST import-jobs", False, None, str(exc)))
    else:
        record(ctx, StepResult("6. POST import-jobs", False, None, "skipped: missing kb/file/token"))

    # 7 poll import job
    if ctx.job_id and ctx.token:
        deadline = time.monotonic() + POLL_TIMEOUT_SEC
        last_payload: dict[str, Any] = {}
        last_status = 0
        try:
            while True:
                r = client.get(f"/v1/import-jobs/{ctx.job_id}", headers=auth_headers(ctx))
                last_status = r.status_code
                last_payload = _json_body(r)
                data = last_payload.get("data") if isinstance(last_payload.get("data"), dict) else {}
                job_status = str(data.get("status", ""))
                if job_status in ("completed", "failed"):
                    break
                if time.monotonic() >= deadline:
                    break
                time.sleep(POLL_INTERVAL_SEC)

            import_terminal_ok = last_status == 200 and job_status == "completed"
            err_msg = data.get("errorMessage") or data.get("error") if isinstance(data, dict) else None
            detail = f"status={job_status}, progress={data.get('progress')}, stage={data.get('stage')}"
            if err_msg:
                detail += f", error={err_msg}"
            notes = ""
            if job_status == "failed":
                notes = "若缺少 OPENAI_API_KEY 或上传文件为空，导入可能失败；请检查服务端日志"
            record(
                ctx,
                StepResult(
                    "7. GET import-jobs poll",
                    import_terminal_ok,
                    last_status,
                    detail,
                    notes=notes,
                ),
            )
        except httpx.HTTPError as exc:
            record(ctx, StepResult("7. GET import-jobs poll", False, None, str(exc)))
    else:
        record(ctx, StepResult("7. GET import-jobs poll", False, None, "skipped: no job_id"))

    chunks_available = import_terminal_ok

    # 8 list chunks
    if ctx.kb_id and ctx.file_id and chunks_available:
        try:
            r = client.get(
                f"/v1/knowledge-bases/{ctx.kb_id}/files/{ctx.file_id}/chunks",
                params={"page": 1, "pageSize": 10},
            )
            payload = _json_body(r)
            data = payload.get("data") if isinstance(payload.get("data"), list) else []
            pagination = payload.get("pagination") if isinstance(payload.get("pagination"), dict) else {}
            total = pagination.get("total")
            if data:
                ctx.data_id = data[0].get("dataId")
            ok = r.status_code == 200 and len(data) >= 1
            detail = f"total={total}, first_dataId={ctx.data_id}"
            if not ok:
                detail = _error_message(payload) or detail
            record(ctx, StepResult("8. GET file chunks list", ok, r.status_code, detail))
        except httpx.HTTPError as exc:
            record(ctx, StepResult("8. GET file chunks list", False, None, str(exc)))
    else:
        reason = "import not completed" if not chunks_available else "missing ids"
        record(
            ctx,
            StepResult("8. GET file chunks list", False, None, f"skipped: {reason}"),
        )

    # 9 get chunk detail
    if ctx.kb_id and ctx.file_id and ctx.data_id:
        try:
            r = client.get(
                f"/v1/knowledge-bases/{ctx.kb_id}/files/{ctx.file_id}/chunks/{ctx.data_id}",
            )
            payload = _json_body(r)
            data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            ok = r.status_code == 200 and bool(data.get("target"))
            detail = f"target.dataId={data.get('target', {}).get('dataId') if isinstance(data.get('target'), dict) else 'n/a'}"
            record(ctx, StepResult("9. GET chunk detail", ok, r.status_code, detail))
        except httpx.HTTPError as exc:
            record(ctx, StepResult("9. GET chunk detail", False, None, str(exc)))
    else:
        record(ctx, StepResult("9. GET chunk detail", False, None, "skipped"))

    # 10 hit-test
    if ctx.kb_id:
        try:
            r = client.post(
                f"/v1/knowledge-bases/{ctx.kb_id}/hit-test",
                json={"query": SMOKE_QUERY, "topK": 3},
            )
            payload = _json_body(r)
            data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            results = data.get("results") if isinstance(data.get("results"), list) else []
            ok = r.status_code == 200
            detail = f"latencyMs={data.get('latencyMs')}, results={len(results)}"
            if ok and not results and job_status == "failed":
                ok = False
                detail += " (no results; import may have failed)"
            if not ok and r.status_code != 200:
                detail = _error_message(payload) or detail
            record(ctx, StepResult("10. POST hit-test", ok, r.status_code, detail))
        except httpx.HTTPError as exc:
            record(ctx, StepResult("10. POST hit-test", False, None, str(exc)))
    else:
        record(ctx, StepResult("10. POST hit-test", False, None, "skipped"))

    # 11 create conversation
    try:
        r = client.post("/v1/conversations", json={"title": f"smoke-conv-{ts}"})
        payload = _json_body(r)
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        ctx.conversation_id = data.get("id") if isinstance(data.get("id"), str) else None
        ok = r.status_code == 201 and bool(ctx.conversation_id)
        detail = f"conversation_id={ctx.conversation_id}"
        record(ctx, StepResult("11. POST /v1/conversations", ok, r.status_code, detail))
    except httpx.HTTPError as exc:
        record(ctx, StepResult("11. POST /v1/conversations", False, None, str(exc)))

    # 12 stream message + citations
    if ctx.conversation_id:
        try:
            kb_ids = [ctx.kb_id] if ctx.kb_id else []
            with client.stream(
                "POST",
                f"/v1/conversations/{ctx.conversation_id}/messages:stream",
                json={
                    "content": SMOKE_QUERY,
                    "modelId": "gpt-5",
                    "knowledgeBaseIds": kb_ids,
                },
                headers={"Accept": "text/event-stream"},
            ) as r:
                body = "".join(r.iter_text())
            events = _parse_sse_events(body)
            names = [n for n, _ in events]
            retrieval = next((d for n, d in events if n == "retrieval.completed"), None)
            citations = retrieval.get("citations", []) if isinstance(retrieval, dict) else []
            cite_ok = False
            cite_detail = f"events={names[-8:]}, citations={len(citations)}"
            if citations:
                c0 = citations[0]
                required = ("index", "kbId", "fileId", "chunkId")
                missing = [k for k in required if k not in c0]
                cite_ok = not missing
                cite_detail += f"; first={ {k: c0.get(k) for k in required} }"
                if missing:
                    cite_detail += f"; missing={missing}"
            ok = r.status_code == 200 and "retrieval.completed" in names and cite_ok
            if r.status_code == 200 and "retrieval.completed" in names and not citations:
                ok = False
                cite_detail += " (empty citations)"
            if "message.failed" in names:
                ok = False
                fail = next((d for n, d in events if n == "message.failed"), {})
                cite_detail += f"; message.failed={fail}"
            record(
                ctx,
                StepResult(
                    "12. POST messages:stream SSE",
                    ok,
                    r.status_code,
                    cite_detail,
                    notes="需要可用的 OpenAI 与已成功索引的 KB 才有 citations",
                ),
            )
        except httpx.HTTPError as exc:
            record(ctx, StepResult("12. POST messages:stream SSE", False, None, str(exc)))
    else:
        record(ctx, StepResult("12. POST messages:stream SSE", False, None, "skipped"))

    client.close()

    passed = sum(1 for s in ctx.results if s.passed)
    failed = len(ctx.results) - passed
    print("\n=== SUMMARY ===")
    print(f"base_url={ctx.base_url}")
    print(f"passed={passed} failed={failed} total={len(ctx.results)}")
    return 0 if failed == 0 else 1


def main() -> None:
    base = os.environ.get("SMOKE_BASE_URL", DEFAULT_BASE_URL)
    code = run_smoke(base)
    sys.exit(code)


if __name__ == "__main__":
    main()
