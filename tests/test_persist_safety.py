from __future__ import annotations

import json

import pytest

from engram import Memory
from engram.embed import HashingEmbedder
from engram.store import DimensionMismatchError, EmbedderMismatchError, IncompatibleStoreError, StoreFormatError


def test_open_rejects_plain_file_instead_of_unpickling(tmp_path):
    path = tmp_path / "legacy.pkl"
    path.write_bytes(b"not a jsonl store and not to be executed")
    with pytest.raises(StoreFormatError):
        Memory.open(str(path))


def test_newer_schema_fails_loudly(tmp_path):
    path = tmp_path / "store"
    path.mkdir()
    (path / "manifest.json").write_text(
        json.dumps({"schema_version": 999, "embedding_dim": 256, "counts": {}}),
        encoding="utf-8",
    )
    with pytest.raises(IncompatibleStoreError):
        Memory.open(str(path))


def test_embedding_dimension_mismatch_fails_loudly(tmp_path):
    path = tmp_path / "store"
    mem = Memory(embedder=HashingEmbedder(8))
    mem.add("I live in Shenzhen.", user_id="u")
    mem.save(str(path))

    with pytest.raises(DimensionMismatchError):
        Memory.open(str(path), embedder=HashingEmbedder(16))


def test_embedding_model_mismatch_fails_loudly_even_with_same_dimension(tmp_path):
    path = tmp_path / "store"
    mem = Memory(embedder=HashingEmbedder(8))
    mem.add("I live in Shenzhen.", user_id="u")
    mem.save(str(path))

    manifest_path = path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["embedder_id"] = "different-embedding-model"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(EmbedderMismatchError):
        Memory.open(str(path), embedder=HashingEmbedder(8))
