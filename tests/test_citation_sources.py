from __future__ import annotations

from app.domain.knowledge_base import SearchHit
from app.services.citation_sources import build_message_sources, citation_from_hit
from app.services.markdown_render import BACKEND_URL_PLACEHOLDER, upload_asset_path


def test_upload_asset_path_encodes_segments() -> None:
    path = upload_asset_path("kb_images/foo bar.png")
    assert path == f"{BACKEND_URL_PLACEHOLDER}/v1/uploads/assets/kb_images/foo%20bar.png"


def test_citation_from_hit_collects_image_keys_from_snippet() -> None:
    hit = SearchHit(
        document_id="file_1",
        file_name="doc.md",
        chunk_id="d000001",
        text="说明 ![图](kb_images/a.png) 更多",
        score=0.5,
        citation={"kb_id": "kb_1", "type": "text"},
    )
    citation = citation_from_hit(hit, index=1)
    assert citation["imageKeys"] == ["kb_images/a.png"]
    assert citation["textFormat"] == "markdown"
    assert "/v1/uploads/assets/kb_images/a.png" in citation["markdown"]
    assert citation["chunkViewPath"] == "/v1/knowledge-bases/kb_1/files/file_1/chunks/d000001"


def test_build_message_sources_groups_files() -> None:
    citations = [
        {
            "index": 1,
            "kbId": "kb_1",
            "fileId": "file_a",
            "chunkId": "d1",
            "type": "text",
            "score": 0.9,
            "snippet": "a",
            "charCount": 1,
            "fileName": "a.pdf",
            "fileViewPath": "/v1/knowledge-bases/kb_1/files/file_a",
        },
        {
            "index": 2,
            "kbId": "kb_1",
            "fileId": "file_a",
            "chunkId": "d2",
            "type": "text",
            "score": 0.8,
            "snippet": "b",
            "charCount": 1,
            "fileName": "a.pdf",
            "fileViewPath": "/v1/knowledge-bases/kb_1/files/file_a",
        },
    ]
    sources = build_message_sources(citations, kb_service=None)
    assert len(sources["segments"]) == 2
    assert len(sources["files"]) == 1
    assert sources["files"][0]["segmentIndexes"] == [1, 2]
