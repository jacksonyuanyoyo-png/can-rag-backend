from __future__ import annotations

from pathlib import Path

from app.core.config import Settings
from app.services.chat_vision import append_citation_figures_to_messages

MINIMAL_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
    b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_append_citation_figures_adds_vision_user_message(tmp_path: Path) -> None:
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    storage_key = "kb_images/fig.png"
    (upload_root / "kb_images").mkdir(parents=True)
    (upload_root / storage_key).write_bytes(MINIMAL_PNG)

    settings = Settings(CHAT_VISION_ENABLED=True, CHAT_VISION_MAX_IMAGES=2)
    base = [
        {"role": "system", "content": "ctx"},
        {"role": "user", "content": "问题？"},
    ]
    citations = [
        {
            "index": 1,
            "storageKey": storage_key,
            "snippet": "图示说明",
            "fileName": "doc.md",
        }
    ]
    enriched = append_citation_figures_to_messages(
        base,
        citations=citations,
        upload_root=upload_root,
        settings=settings,
    )
    assert len(enriched) == 3
    figure_message = enriched[1]
    assert figure_message["role"] == "user"
    content = figure_message["content"]
    assert isinstance(content, list)
    assert any(part.get("type") == "image_url" for part in content)
    assert enriched[2]["content"] == "问题？"


def test_append_citation_figures_skipped_when_disabled(tmp_path: Path) -> None:
    settings = Settings(CHAT_VISION_ENABLED=False)
    base = [{"role": "user", "content": "hi"}]
    out = append_citation_figures_to_messages(
        base,
        citations=[{"index": 1, "storageKey": "kb_images/x.png"}],
        upload_root=tmp_path,
        settings=settings,
    )
    assert out == base
