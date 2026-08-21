"""Idempotent batch import (CLAUDE.md §8 "no silent corruption", applied to ingestion): re-posting the
same export must not double every memory. Fingerprints persist with the snapshot, so dedupe survives a
restart. All offline."""
from __future__ import annotations

import os
import shutil
import tempfile

from engram.embed import HashingEmbedder
from engram.memory import Memory
from engram.service import MemoryService

_SESSIONS = [
    {"session_id": "s1", "messages": [{"role": "user", "content": "I have a dog named Mochi."}]},
    {"session_id": "s2", "messages": [{"role": "user", "content": "I drive a Tesla Model 3."}]},
]


def test_reimport_is_skipped():
    m = Memory(embedder=HashingEmbedder(64))
    first = m.import_messages(_SESSIONS, user_id="u")
    assert first["episodes"] == 2 and first["skipped"] == 0
    again = m.import_messages(_SESSIONS, user_id="u")
    assert again["episodes"] == 0 and again["skipped"] == 2
    assert len(m.episodes_doc.values()) == 2  # not doubled


def test_dedupe_off_restores_append_behavior():
    m = Memory(embedder=HashingEmbedder(64))
    m.import_messages(_SESSIONS, user_id="u")
    raw = m.import_messages(_SESSIONS, user_id="u", dedupe=False)
    assert raw["episodes"] == 2  # explicit opt-out duplicates (the legacy escape hatch)
    assert len(m.episodes_doc.values()) == 4


def test_fingerprints_survive_restart():
    d = tempfile.mkdtemp(prefix="engram_dedupe_")
    try:
        p = os.path.join(d, "u.pkl")
        m = Memory.open(p, embedder=HashingEmbedder(64))
        m.import_messages(_SESSIONS, user_id="u")
        m.save()
        m2 = Memory.open(p, embedder=HashingEmbedder(64))  # fresh process
        again = m2.import_messages(_SESSIONS, user_id="u")
        assert again["skipped"] == 2 and len(m2.episodes_doc.values()) == 2
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_new_sessions_still_ingest_alongside_skips():
    m = Memory(embedder=HashingEmbedder(64))
    m.import_messages(_SESSIONS, user_id="u")
    mixed = m.import_messages(
        _SESSIONS + [{"session_id": "s3", "messages": [{"role": "user", "content": "I started judo."}]}],
        user_id="u")
    assert mixed["skipped"] == 2 and mixed["episodes"] == 1
    assert len(m.episodes_doc.values()) == 3


def test_service_import_is_idempotent():
    d = tempfile.mkdtemp(prefix="engram_dedupesvc_")
    try:
        svc = MemoryService(data_dir=d, embedder_name="hashing", llm_name="")
        first = svc.import_("u", sessions=_SESSIONS)
        again = svc.import_("u", sessions=_SESSIONS)
        assert first["episodes"] == 2
        assert again["episodes"] == 0 and again["skipped"] == 2
    finally:
        shutil.rmtree(d, ignore_errors=True)
