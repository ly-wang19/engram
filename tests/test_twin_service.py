from __future__ import annotations

import json
import sqlite3

import pytest

from engram import Memory
from engram.service import MemoryService
from engram.store import StoreFormatError


def _mutate_private_state(path: str, mutate) -> None:
    with sqlite3.connect(f"{path}/store.sqlite3") as connection:
        row = connection.execute(
            "SELECT payload FROM metadata WHERE key='state'"
        ).fetchone()
        assert row is not None
        state = json.loads(row[0])
        mutate(state)
        connection.execute(
            "UPDATE metadata SET payload=? WHERE key='state'",
            (json.dumps(state, ensure_ascii=False, sort_keys=True),),
        )


def test_twin_service_is_default_deny_and_contract_is_versioned(tmp_path) -> None:
    service = MemoryService(data_dir=str(tmp_path), embedder_name="hashing", llm_name="")

    denied = service.authorize_twin_action(
        "u1",
        capability="calendar",
        permission="observe",
        resource="calendars/personal/events",
    )
    assert denied["decision"]["status"] == "denied"
    assert denied["executed"] is False

    before = service.twin_contract("u1")["contract"]
    revised = service.revise_twin_contract(
        "u1",
        {
            "goals": [
                {
                    "title": "Protect focused work",
                    "description": "Keep mornings meeting-free",
                    "provenance": ["owner:interview-1"],
                }
            ],
            "principles": [
                {
                    "name": "Reversibility",
                    "statement": "Prefer reversible actions",
                    "provenance": ["owner:interview-1"],
                }
            ],
            "provenance": ["owner:revision-1"],
        },
    )
    assert revised["contract"]["version"] == before["version"] + 1
    assert revised["model_context"]["goals"][0]["title"] == "Protect focused work"
    history = service.twin_contract_history("u1")["contracts"]
    assert [item["version"] for item in history] == [2, 1]

    reloaded = MemoryService(data_dir=str(tmp_path), embedder_name="hashing", llm_name="")
    reloaded_history = reloaded.twin_contract_history("u1")["contracts"]
    assert reloaded_history == history


def test_capability_approval_and_action_outcome_roundtrip(tmp_path) -> None:
    service = MemoryService(data_dir=str(tmp_path), embedder_name="hashing", llm_name="")
    grant = service.grant_capability(
        "u1",
        capability="calendar",
        permission="execute",
        scopes=["calendars/personal/**"],
        credential_ref={"provider": "macos-keychain", "key": "engram/calendar"},
        provenance=["owner:grant-1"],
    )["grant"]

    pending = service.authorize_twin_action(
        "u1",
        capability="calendar",
        permission="execute",
        resource="calendars/personal/events/42",
        external_write=True,
    )
    assert pending["decision"]["status"] == "requires_confirmation"

    with pytest.raises(ValueError, match="owner confirmation endpoint"):
        service.authorize_twin_action(
            "u1",
            capability="calendar",
            permission="execute",
            resource="calendars/personal/events/42",
            external_write=True,
            human_confirmed=True,
        )

    allowed = service.confirm_twin_action("u1", pending["decision"]["id"])
    assert allowed["decision"]["status"] == "allowed"
    assert allowed["decision"]["confirmed_at"] is not None
    assert allowed["executable"] is True
    status = service.twin_decision("u1", allowed["decision"]["id"])
    assert status["executable"] is True and status["executed"] is False
    recorded = service.record_twin_action(
        "u1",
        allowed["decision"]["id"],
        "Created owner-approved calendar event",
        provenance=["executor:calendar-1"],
    )
    assert recorded["ok"] is True
    assert recorded["action"]["outcome"].startswith("Created owner-approved")

    reloaded = MemoryService(
        data_dir=str(tmp_path),
        embedder_name="hashing",
        llm_name="",
    )
    memory = reloaded.get("u1")
    assert memory.capability_registry.grants[0].id == grant["id"]
    assert memory.capability_registry.grants[0].credential_ref.key == "engram/calendar"
    assert allowed["decision"]["id"] in memory.twin_decisions
    assert memory.twin_actions[0].outcome.startswith("Created owner-approved")
    assert reloaded.twin_decision("u1", allowed["decision"]["id"])["executed"] is True


def test_model_context_never_exposes_capability_or_credential_controls(tmp_path) -> None:
    service = MemoryService(data_dir=str(tmp_path), embedder_name="hashing", llm_name="")
    service.grant_capability(
        "u1",
        capability="mail",
        permission="execute",
        scopes=["mailboxes/personal/**"],
        credential_ref={"provider": "vault", "key": "mail/main"},
    )
    payload = service.twin_contract("u1")

    rendered = str(payload["model_context"])
    assert "credential" not in rendered
    assert "vault" not in rendered
    assert "mailboxes" not in rendered
    agent_grant = service.capabilities("u1")["registry"]["grants"][0]
    assert "credential_ref" not in agent_grant
    assert agent_grant["credential_configured"] is True
    assert service.capabilities(
        "u1", include_credential_refs=True
    )["registry"]["grants"][0]["credential_ref"] == {
        "provider": "vault",
        "key": "mail/main",
    }


def test_persistence_rejects_contract_history_with_a_missing_revision(tmp_path) -> None:
    service = MemoryService(data_dir=str(tmp_path), embedder_name="hashing", llm_name="")
    service.revise_twin_contract("u1", {"provenance": ["owner:revision-2"]})
    path = service._path("u1")

    def remove_revision_number(state):
        state["twin_contract_history"][-1]["version"] = 3
        state["twin_contract"]["version"] = 3

    _mutate_private_state(path, remove_revision_number)
    with pytest.raises(StoreFormatError, match="contain every revision"):
        Memory.open(path)


def test_persistence_rejects_action_audit_detached_from_canonical_decision(tmp_path) -> None:
    service = MemoryService(data_dir=str(tmp_path), embedder_name="hashing", llm_name="")
    service.grant_capability(
        "u1",
        capability="calendar",
        permission="observe",
        scopes=["calendars/personal/**"],
    )
    allowed = service.authorize_twin_action(
        "u1",
        capability="calendar",
        permission="observe",
        resource="calendars/personal/events/42",
    )
    service.record_twin_action("u1", allowed["decision"]["id"], "executor reported success")
    path = service._path("u1")

    def forge_embedded_decision(state):
        state["twin_actions"][0]["decision"]["reason"] = "forged audit reason"

    _mutate_private_state(path, forge_embedded_decision)
    with pytest.raises(StoreFormatError, match="canonical decision"):
        Memory.open(path)
