"""Document ingestion (CLAUDE.md §6 adoption layer) — turn an uploaded file into an Engram session.

Documents (PDF/DOCX/text) are flattened to text and ingested through the SAME `Memory.import_messages`
path as chat history; images are captioned (by the service layer, which holds the vision model) and the
caption text is ingested instead. This module is pure-stdlib and does NOT caption or embed: it only
detects the file kind and extracts text. PDF/DOCX extraction needs optional deps (`engram-memory[documents]`)
and raises a clear, actionable ImportError when they're missing — the core/offline path never imports them.
"""
from __future__ import annotations

import os
from typing import Optional

from .base import ImportMessage, ImportSession

_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


def _as_bytes(data) -> bytes:
    return bytes(data) if isinstance(data, (bytes, bytearray)) else str(data).encode("utf-8")


def detect_kind(data, filename: Optional[str] = None, content_type: Optional[str] = None) -> str:
    """One of: 'image' | 'pdf' | 'docx' | 'text'. Decided from content-type, then filename extension, then
    magic bytes — so it works whether the caller knows the MIME type or only has raw bytes."""
    ct = (content_type or "").lower()
    ext = os.path.splitext(filename or "")[1].lower()
    head = _as_bytes(data)[:8] if data is not None else b""
    if ct.startswith("image/") or ext in _IMAGE_EXT or head.startswith(b"\x89PNG") or head[:3] == b"\xff\xd8\xff":
        return "image"
    if ct == "application/pdf" or ext == ".pdf" or head[:4] == b"%PDF":
        return "pdf"
    if ext == ".docx" or "officedocument.wordprocessing" in ct:
        return "docx"
    return "text"


def extract_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as e:  # optional dep — keep it out of the zero-setup core
        raise ImportError(
            "PDF import needs pypdf — install with `pip install \"engram-memory[documents]\"` (or `pip install pypdf`)."
        ) from e
    import io

    reader = PdfReader(io.BytesIO(_as_bytes(data)))
    return "\n\n".join((page.extract_text() or "") for page in reader.pages).strip()


def extract_docx(data: bytes) -> str:
    try:
        import docx  # python-docx
    except ImportError as e:
        raise ImportError(
            "DOCX import needs python-docx — install with `pip install \"engram-memory[documents]\"`."
        ) from e
    import io

    doc = docx.Document(io.BytesIO(_as_bytes(data)))
    return "\n".join(p.text for p in doc.paragraphs).strip()


def document_text(data, kind: str) -> str:
    """Extract plain text from a non-image document (raises ImportError for PDF/DOCX without the extra)."""
    if kind == "pdf":
        return extract_pdf(data)
    if kind == "docx":
        return extract_docx(data)
    if isinstance(data, (bytes, bytearray)):
        return data.decode("utf-8", errors="replace").strip()
    return str(data).strip()


def to_data_url(data, content_type: Optional[str] = None) -> str:
    """Encode raw image bytes as a data: URL the vision captioner accepts."""
    import base64

    ct = content_type or "image/png"
    return f"data:{ct};base64,{base64.b64encode(_as_bytes(data)).decode('ascii')}"


def to_session(text: str, *, filename: Optional[str] = None, session_id: str = "document",
               metadata: Optional[dict] = None) -> ImportSession:
    """Wrap extracted text (or a caption) as a one-message ImportSession ready for import_messages."""
    meta = {"source": "document", **(metadata or {})}
    msg = ImportMessage(content=text, speaker="document", metadata=meta)
    return ImportSession(session_id=session_id, messages=[msg], title=filename or "", metadata=meta)
