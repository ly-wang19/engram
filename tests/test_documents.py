"""Multimodal ingestion tests (CLAUDE.md §6): document text extraction + image captioning, all offline.

Documents flatten to text and an image becomes its caption — both ride the normal import pipeline, so the
real assertion is "it's now searchable memory". Captioning is exercised with a stub captioner (a tiny
object exposing `.caption`) so no real vision model is needed; the no-captioner path must degrade to a
placeholder, never crash (the zero-setup invariant)."""
from __future__ import annotations

import importlib.util
import shutil
import tempfile

import pytest

from engram.connectors import documents as D
from engram.llm import vision
from engram.server import openai_compat as oc
from engram.service import MemoryService

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32  # just enough header for magic-byte detection


class _StubCaptioner:
    """Stand-in vision model: anything with a `.caption(ref, prompt)` is a captioner."""
    def caption(self, image_ref, prompt=None):  # noqa: ARG002
        return "a red bicycle leaning on a wall"


@pytest.fixture()
def svc():
    d = tempfile.mkdtemp(prefix="engram_doc_")
    try:
        yield MemoryService(data_dir=d, embedder_name="hashing", llm_name="")  # captioner=None offline
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --- pure helpers (no service) ----------------------------------------------


def test_detect_kind():
    assert D.detect_kind(b"%PDF-1.7 ...", "a.pdf", "application/pdf") == "pdf"
    assert D.detect_kind(_PNG, "a.png", "image/png") == "image"
    assert D.detect_kind(b"\xff\xd8\xff\xe0", None, None) == "image"  # JPEG magic, no hints
    assert D.detect_kind("plain notes", "n.txt", "text/plain") == "text"
    assert D.detect_kind(b"PK\x03\x04", "a.docx", None) == "docx"


def test_vision_caption_image_guards():
    assert vision.caption_image(None, "x") == ""           # no captioner
    assert vision.caption_image(object(), "x") == ""        # not vision-capable (no .caption)
    assert vision.caption_image(_StubCaptioner(), "x") == "a red bicycle leaning on a wall"


def test_vision_image_refs_and_content():
    content = [{"type": "text", "text": "hi"}, {"type": "image_url", "image_url": {"url": "u1"}}]
    assert vision.image_refs(content) == ["u1"]
    assert vision.caption_content(_StubCaptioner(), content) == ["[image] a red bicycle leaning on a wall"]
    assert vision.caption_content(None, content) == ["[image]"]  # placeholder when no captioner


# --- document import through the service ------------------------------------


def test_text_document_is_ingested_and_searchable(svc):
    res = svc.import_document("u", "Apollo program notes. Lead engineer: Wei Zhang. Launched 1969.",
                              filename="notes.txt", content_type="text/plain")
    assert res["ok"] is True and res["kind"] == "text"
    mem = svc.memories("u", include_sensitive=True)
    assert mem["counts"]["episodes"] >= 1
    assert any("Apollo" in ep["content"] for ep in mem["episodes"])
    # and it's retrievable as a lean context
    ctx = svc.recall("u", "Apollo program", lean=True)["context"]
    assert "Apollo" in ctx


def test_image_is_captioned_into_memory(svc):
    svc.captioner = _StubCaptioner()  # pretend a vision model is configured
    res = svc.import_document("u", _PNG, filename="bike.png", content_type="image/png")
    assert res["ok"] is True and res["kind"] == "image"
    mem = svc.memories("u", include_sensitive=True)
    assert any("red bicycle" in ep["content"] for ep in mem["episodes"])


def test_image_without_captioner_stores_placeholder(svc):
    # captioner is None (offline) -> a placeholder so the image still leaves a trace, no crash
    res = svc.import_document("u", _PNG, filename="x.png", content_type="image/png")
    assert res["ok"] is True and res["kind"] == "image"
    mem = svc.memories("u", include_sensitive=True)
    assert any("[image]" in ep["content"] for ep in mem["episodes"])


def test_pdf_without_pypdf_returns_actionable_error(svc):
    if importlib.util.find_spec("pypdf") is not None:
        pytest.skip("pypdf installed — the missing-dep error path isn't exercised")
    res = svc.import_document("u", b"%PDF-1.4 not-really", filename="a.pdf", content_type="application/pdf")
    assert res["ok"] is False and "pypdf" in res["error"]


# --- OpenAI-compat: images sent through the proxy get remembered as captions --


def test_remembered_text_plain_is_unchanged():
    msgs = [{"role": "user", "content": "hello world"}]
    assert oc.remembered_text(None, msgs) == "hello world" == oc.latest_user_text(msgs)


def test_remembered_text_appends_image_caption():
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "what is this?"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}]}]
    out = oc.remembered_text(_StubCaptioner(), msgs)
    assert "what is this?" in out and "[image] a red bicycle leaning on a wall" in out


def test_remembered_text_image_only_without_captioner():
    msgs = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "http://x/i.png"}}]}]
    assert oc.remembered_text(None, msgs) == "[image]"
