"""LanceDB tenant prefiltering — the change that lets a vector backend actually be an index.

Before this, the only way to say "this user's facts" was a Python predicate, which LanceDB cannot see
inside, so every multi-tenant search materialised the whole table. Since multi-tenant retrieval always
filters by user, no search was ever sub-linear.

The load-bearing test is `test_prefilter_finds_hits_beyond_the_unfiltered_neighbourhood`: it is designed
to FAIL if the filter is applied after the ANN query instead of inside it.
"""
from __future__ import annotations

import pytest

from engram.types import Fact
from engram.util import now

lancedb = pytest.importorskip("lancedb")

from engram.store.lancedb_store import _TENANT_COL, LanceDBVectorStore  # noqa: E402


def _fact(user: str, text: str, vec: list[float]) -> Fact:
    return Fact(
        user_id=user, subject="s", predicate="p", object=text, text=text,
        valid_at=now(), embedding=vec,
    )


def test_tenant_filter_returns_only_that_tenant(tmp_path):
    store = LanceDBVectorStore(str(tmp_path / "db"), "facts")
    for user in ("alice", "bob"):
        for i in range(3):
            f = _fact(user, f"{user} fact {i}", [1.0, float(i), 0.0])
            store.upsert(f.id, f.embedding or [], f)

    hits = store.search([1.0, 0.0, 0.0], 10, user_id="alice")
    assert hits, "tenant search must return the tenant's own rows"
    assert {p.user_id for _s, p in hits} == {"alice"}


def test_prefilter_finds_hits_beyond_the_unfiltered_neighbourhood(tmp_path):
    """The correctness property a post-filter cannot satisfy.

    One tenant's rows sit far from the query; the other tenant fills the entire nearest neighbourhood.
    Filtering *after* a top_k ANN query would return nothing, because none of the k nearest rows belong
    to the tenant asked for. Only a filter applied inside the search can find them.
    """
    store = LanceDBVectorStore(str(tmp_path / "db"), "facts")
    query = [1.0, 0.0, 0.0]

    for i in range(50):  # noisy majority tenant, all near the query
        f = _fact("loud", f"loud {i}", [1.0, 0.001 * i, 0.0])
        store.upsert(f.id, f.embedding or [], f)
    wanted = []
    for i in range(3):  # quiet tenant, all far from the query
        f = _fact("quiet", f"quiet {i}", [0.0, 1.0, 0.05 * i])
        store.upsert(f.id, f.embedding or [], f)
        wanted.append(f.id)

    hits = store.search(query, 3, user_id="quiet")
    assert {p.id for _s, p in hits} == set(wanted), (
        "tenant filter must run inside the search; a post-filter would return nothing here"
    )


def test_tenant_column_is_written(tmp_path):
    store = LanceDBVectorStore(str(tmp_path / "db"), "facts")
    f = _fact("alice", "hello", [1.0, 0.0, 0.0])
    store.upsert(f.id, f.embedding or [], f)
    rows = store._open().to_arrow().to_pylist()
    assert rows[0][_TENANT_COL] == "alice"


def test_python_predicate_still_supported(tmp_path):
    """The general escape hatch must keep working (it scans, by necessity)."""
    store = LanceDBVectorStore(str(tmp_path / "db"), "facts")
    for user in ("alice", "bob"):
        f = _fact(user, f"{user} note", [1.0, 0.0, 0.0])
        store.upsert(f.id, f.embedding or [], f)

    hits = store.search([1.0, 0.0, 0.0], 10, where=lambda p: p.user_id == "bob")
    assert {p.user_id for _s, p in hits} == {"bob"}


def test_legacy_table_without_tenant_column_still_works(tmp_path):
    """A store written by an earlier release has no tenant column. It must keep reading and writing —
    falling back to a scan — rather than failing on a schema mismatch."""
    path = str(tmp_path / "db")
    db = lancedb.connect(path)
    legacy = _fact("alice", "legacy row", [1.0, 0.0, 0.0])
    from engram.store.lancedb_store import _encode_payload

    db.create_table(
        "facts",
        data=[{"key": legacy.id, "vector": legacy.embedding, "payload": _encode_payload(legacy)}],
        mode="overwrite",
    )

    store = LanceDBVectorStore(path, "facts")
    assert store._has_tenant_column(store._open()) is False

    fresh = _fact("alice", "new row", [1.0, 0.01, 0.0])
    store.upsert(fresh.id, fresh.embedding or [], fresh)  # must not raise on the old schema

    hits = store.search([1.0, 0.0, 0.0], 10, user_id="alice")
    assert {p.id for _s, p in hits} == {legacy.id, fresh.id}
