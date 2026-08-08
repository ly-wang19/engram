import json

import pytest

from engram.twin import (
    ActionRecord,
    ActionRequest,
    Boundary,
    BoundaryEffect,
    CapabilityGrant,
    CapabilityRegistry,
    CredentialRef,
    DecisionStatus,
    Goal,
    PermissionLevel,
    Principle,
    TwinContract,
    scope_matches,
)


def _request(**overrides):
    values = {
        "capability": "calendar",
        "permission": PermissionLevel.OBSERVE,
        "resource": "calendars/personal/events",
    }
    values.update(overrides)
    return ActionRequest(**values)


def test_registry_is_default_deny_and_permissions_are_hierarchical():
    contract = TwinContract()
    registry = CapabilityRegistry()
    assert registry.decide(_request(), contract).status is DecisionStatus.DENIED

    registry.add(
        CapabilityGrant(
            capability="calendar",
            permission=PermissionLevel.DRAFT,
            scopes=("calendars/personal/**",),
        )
    )
    assert registry.decide(_request(), contract).allowed
    assert registry.decide(_request(permission=PermissionLevel.DRAFT), contract).allowed
    execute = registry.decide(_request(permission=PermissionLevel.EXECUTE), contract)
    assert execute.status is DecisionStatus.DENIED


def test_expired_and_revoked_grants_are_denied():
    contract = TwinContract()
    expired = CapabilityGrant(
        capability="calendar",
        permission=PermissionLevel.OBSERVE,
        scopes=("calendars/personal/**",),
        granted_at=10,
        expires_at=20,
    )
    registry = CapabilityRegistry([expired])
    assert registry.decide(_request(), contract, at=20).status is DecisionStatus.DENIED

    active = CapabilityGrant(
        capability="calendar",
        permission=PermissionLevel.OBSERVE,
        scopes=("calendars/personal/**",),
        granted_at=10,
    )
    registry = CapabilityRegistry([active])
    assert registry.decide(_request(), contract, at=20).allowed
    registry.revoke(active.id, at=21)
    assert registry.decide(_request(), contract, at=21).status is DecisionStatus.DENIED


def test_scope_matching_rejects_prefix_and_wildcard_bypasses():
    assert scope_matches("accounts/alice/**", "accounts/alice/mail/inbox")
    assert scope_matches("accounts/*/profile", "accounts/alice/profile")
    assert scope_matches("accounts/*/**", "accounts/alice/mail/inbox")
    assert not scope_matches("accounts/alice", "accounts/alice-private")
    assert not scope_matches("accounts/alice/**", "accounts/alice-private/mail")
    assert not scope_matches("accounts/*/profile", "accounts/alice/private/profile")
    with pytest.raises(ValueError):
        scope_matches("accounts/alice*", "accounts/alice-private")
    with pytest.raises(ValueError):
        scope_matches("accounts/**", "accounts/../admin")
    with pytest.raises(ValueError):
        scope_matches("accounts/**", "accounts/*")
    with pytest.raises(ValueError):
        scope_matches("accounts/alice/**", "accounts/alice%2F..%2Fadmin")
    with pytest.raises(ValueError):
        scope_matches("accounts/alice/**", "accounts/alice%5c..%5cadmin")
    with pytest.raises(ValueError):
        scope_matches("accounts/alice/**", "accounts/alice/\x00admin")
    with pytest.raises(ValueError):
        scope_matches("accounts/alice/**", "accounts\\alice\\admin")

    registry = CapabilityRegistry(
        [
            CapabilityGrant(
                capability="calendar",
                permission=PermissionLevel.EXECUTE,
                scopes=("calendars/personal/**",),
                granted_at=0,
            )
        ]
    )
    malformed = _request(resource="calendars/personal%2f../admin")
    assert registry.decide(malformed, TwinContract()).status is DecisionStatus.DENIED


def test_external_write_and_high_risk_execution_require_human_confirmation():
    contract = TwinContract()
    registry = CapabilityRegistry(
        [
            CapabilityGrant(
                capability="calendar",
                permission=PermissionLevel.EXECUTE,
                scopes=("calendars/personal/**",),
                granted_at=0,
            )
        ]
    )
    request = _request(
        permission=PermissionLevel.EXECUTE,
        external_write=True,
        high_risk=True,
    )
    pending = registry.decide(request, contract)
    assert pending.status is DecisionStatus.REQUIRES_CONFIRMATION
    allowed = registry.decide(request, contract, human_confirmed=True, at=100)
    assert allowed.allowed
    assert allowed.confirmed_at == 100
    assert allowed.valid_until == 400
    assert allowed.is_fresh(400)
    assert not allowed.is_fresh(400.001)

    record = ActionRecord(request=request, decision=pending)
    assert record.executed_at is None
    with pytest.raises(ValueError):
        ActionRecord(request=request, decision=pending, executed_at=100)
    with pytest.raises(ValueError, match="expired"):
        ActionRecord(request=request, decision=allowed, executed_at=401)
    with pytest.raises(ValueError, match="before"):
        ActionRecord(request=request, decision=allowed, executed_at=99)


def test_owner_boundary_can_deny_or_add_an_approval_gate():
    grant = CapabilityGrant(
        capability="mail",
        permission=PermissionLevel.EXECUTE,
        scopes=("mailboxes/personal/**",),
    )
    registry = CapabilityRegistry([grant])
    request = ActionRequest(
        capability="mail",
        permission=PermissionLevel.EXECUTE,
        resource="mailboxes/personal/messages/42",
    )
    deny = TwinContract(
        boundaries=(
            Boundary(
                description="Never delete messages",
                effect=BoundaryEffect.DENY,
                capability="mail",
                scopes=("mailboxes/personal/messages/*",),
                minimum_permission=PermissionLevel.EXECUTE,
            ),
        )
    )
    assert registry.decide(request, deny).status is DecisionStatus.DENIED

    gate = TwinContract(
        boundaries=(
            Boundary(
                description="Ask before sending",
                effect=BoundaryEffect.REQUIRE_CONFIRMATION,
                capability="mail",
                scopes=("mailboxes/personal/**",),
                minimum_permission=PermissionLevel.EXECUTE,
            ),
        ),
        confirm_external_writes=False,
    )
    assert registry.decide(request, gate).requires_human_confirmation
    assert registry.decide(request, gate, human_confirmed=True).allowed


def test_contract_updates_and_v1_serialization_are_compatible():
    original = TwinContract(
        goals=(Goal("Build a durable personal memory", provenance=("owner:1",)),),
        principles=(Principle("Prefer reversible actions", name="Reversibility"),),
        updated_at=100,
        provenance=("interview:1",),
    )
    updated = original.update(
        goals=original.goals + (Goal("Keep the owner in control"),),
        provenance=("interview:1", "owner-edit:2"),
        updated_at=100,
    )
    assert updated.version == original.version + 1
    assert updated.updated_at > original.updated_at
    assert len(original.goals) == 1

    payload = updated.to_dict()
    payload["future_additive_field"] = {"safe": True}
    restored = TwinContract.from_dict(json.loads(json.dumps(payload)))
    assert restored == updated

    legacy_minimal = TwinContract.from_dict({"goals": [{"title": "Legacy goal"}]})
    assert legacy_minimal.schema_version == 1
    assert legacy_minimal.version == 1
    assert legacy_minimal.goals[0].title == "Legacy goal"
    with pytest.raises(ValueError):
        TwinContract.from_dict({"schema_version": 2})


def test_registry_serializes_credential_reference_but_safe_context_hides_controls():
    credential = CredentialRef(provider="macos-keychain", key="engram/calendar-primary")
    registry = CapabilityRegistry(
        [
            CapabilityGrant(
                capability="calendar",
                permission=PermissionLevel.EXECUTE,
                scopes=("calendars/personal/**",),
                credential_ref=credential,
                provenance=("owner-grant:1",),
            )
        ]
    )
    registry_payload = registry.to_dict()
    assert registry_payload["grants"][0]["permission"] == "execute"
    assert registry_payload["grants"][0]["credential_ref"] == {
        "provider": "macos-keychain",
        "key": "engram/calendar-primary",
    }
    restored = CapabilityRegistry.from_dict(json.loads(json.dumps(registry_payload)))
    assert restored.grants[0].credential_ref == credential

    contract = TwinContract(
        goals=(Goal("Protect focused work"),),
        principles=(Principle("Ask before irreversible actions"),),
        boundaries=(
            Boundary("Do not disclose private correspondence", model_visible=True),
            Boundary(
                "hidden policy rule",
                capability="calendar",
                scopes=("calendars/personal/**",),
                model_visible=False,
            ),
        ),
    )
    model_context = contract.to_model_context()
    encoded = json.dumps(model_context)
    assert "credential" not in encoded
    assert "macos-keychain" not in encoded
    assert "calendar" not in encoded
    assert "scope" not in encoded
    assert "effect" not in encoded
    assert "hidden policy rule" not in encoded
    assert "Do not disclose private correspondence" in encoded


def test_credential_ref_has_no_secret_field_and_rejects_secret_shaped_values():
    assert not hasattr(CredentialRef(provider="vault", key="calendar/main"), "secret")
    with pytest.raises(ValueError):
        CredentialRef(provider="vault", key="sk-live actual secret")
