from __future__ import annotations

from engram import Memory
from engram.ingest import IdentityResolver


def _memory_with_split_identity() -> Memory:
    mem = Memory()
    mem.add("Legacy account episode about the Atlas launch.", user_id="z-user", session_id="legacy")
    mem.add(
        "Primary account episode about the Beacon launch.",
        user_id="a-user",
        session_id="primary",
    )
    mem.add_fact("Casey", "works_at", "Acme Labs", user_id="z-user")
    mem.add_fact("Casey", "lives_in", "Hangzhou", user_id="a-user")
    mem.remember_working("legacy temporary note", user_id="z-user", session_id="legacy")
    mem.remember_working("primary temporary note", user_id="a-user", session_id="primary")
    return mem


def _assert_component_data_is_visible(mem: Memory) -> None:
    for handle in ("a-user", "z-user"):
        episodes = mem.retrieve_episodes("launch", user_id=handle, k=10, pool=10)
        assert {ep.session_id for ep in episodes} == {"legacy", "primary"}

        assert "Acme Labs" in mem.search("Where does Casey work?", user_id=handle).answer()
        assert "Hangzhou" in mem.search("Where does Casey live?", user_id=handle).answer()

        assert {item.content for item in mem.working_memory(handle)} == {
            "legacy temporary note",
            "primary temporary note",
        }

        graph = mem.graph_data(handle)
        assert {edge["fact_text"] for edge in graph["edges"]} == {
            "Casey works at Acme Labs",
            "Casey lives in Hangzhou",
        }
        casey_nodes = [
            entity
            for entity in mem.graph.entities.values()
            if entity.user_id == mem.resolver.resolve(handle) and entity.name == "Casey"
        ]
        assert len(casey_nodes) == 1


def test_identity_resolver_exposes_a_stable_alias_component() -> None:
    resolver = IdentityResolver()
    resolver.resolve("z-user")
    resolver.resolve("a-user")

    assert resolver.link("z-user", "a-user") == "a-user"
    assert resolver.component("z-user") == frozenset({"a-user", "z-user"})

    assert resolver.link("middle-user", "z-user") == "a-user"
    assert resolver.component("middle-user") == frozenset({
        "a-user",
        "middle-user",
        "z-user",
    })


def test_link_identity_keeps_existing_data_from_both_aliases_retrievable() -> None:
    mem = _memory_with_split_identity()

    assert mem.link_identity("z-user", "a-user") == "a-user"

    _assert_component_data_is_visible(mem)


def test_linked_identity_component_survives_persistence_roundtrip(tmp_path) -> None:
    mem = _memory_with_split_identity()
    mem.link_identity("z-user", "a-user")
    path = str(tmp_path / "memory")
    mem.save(path)

    loaded = Memory.open(path)

    assert loaded.resolver.resolve("z-user") == "a-user"
    assert loaded.resolver.component("z-user") == frozenset({"a-user", "z-user"})
    _assert_component_data_is_visible(loaded)


def test_open_repairs_a_pre_fix_snapshot_with_split_component_ownership(tmp_path) -> None:
    mem = _memory_with_split_identity()
    # Old snapshots could contain a linked resolver while payloads retained their pre-merge owners.
    mem.resolver.link("z-user", "a-user")
    path = str(tmp_path / "legacy-memory")
    mem.save(path)

    loaded = Memory.open(path)

    _assert_component_data_is_visible(loaded)
