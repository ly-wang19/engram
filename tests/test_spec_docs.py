from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_durable_persistence_contract_matches_committed_prefix_semantics():
    contract = (ROOT / "specs/001-durable-persistence/contracts/on-disk-format.md").read_text(
        encoding="utf-8"
    )
    assert "manifest-declared committed prefix" in contract
    assert "inside" in contract and "StoreFormatError" in contract
    assert "after" in contract and "torn tail" in contract


def test_durable_persistence_quickstart_documents_both_crash_edges():
    quickstart = (ROOT / "specs/001-durable-persistence/quickstart.md").read_text(encoding="utf-8")
    assert "after the manifest count" in quickstart
    assert "manifest-committed prefix raises `StoreFormatError`" in quickstart
