"""Governance primitives for a personal AI twin.

This module deliberately does not execute tools.  It defines the contract and
authorization decision that an executor must check before acting.  Keeping the
policy layer separate makes the safe default unambiguous: no active, matching
grant means no capability.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, replace
from enum import Enum
from typing import Any, Iterable, Mapping, Optional

from .util import gen_id, now


# An authorization is a short-lived, one-shot control-plane decision, not a
# durable capability token. Executors must check it immediately before acting.
DECISION_TTL_SECONDS = 300.0


class PermissionLevel(str, Enum):
    """Increasing levels of authority; higher levels include lower ones."""

    OBSERVE = "observe"
    DRAFT = "draft"
    EXECUTE = "execute"

    @property
    def rank(self) -> int:
        return {
            PermissionLevel.OBSERVE: 1,
            PermissionLevel.DRAFT: 2,
            PermissionLevel.EXECUTE: 3,
        }[self]

    def includes(self, requested: "PermissionLevel") -> bool:
        return self.rank >= requested.rank


class BoundaryEffect(str, Enum):
    DENY = "deny"
    REQUIRE_CONFIRMATION = "require_confirmation"


class DecisionStatus(str, Enum):
    DENIED = "denied"
    REQUIRES_CONFIRMATION = "requires_confirmation"
    ALLOWED = "allowed"


@dataclass(frozen=True)
class Goal:
    title: str
    description: str = ""
    status: str = "active"
    priority: int = 0
    provenance: tuple[str, ...] = ()
    id: str = field(default_factory=lambda: gen_id("goal"))


@dataclass(frozen=True)
class Principle:
    """A value or decision principle the twin should follow."""

    statement: str
    name: str = ""
    priority: int = 0
    provenance: tuple[str, ...] = ()
    id: str = field(default_factory=lambda: gen_id("principle"))


@dataclass(frozen=True)
class Boundary:
    """A hard denial or an extra human-confirmation rule.

    Scopes use slash-separated segments.  ``*`` matches one complete segment
    and a final ``**`` matches zero or more complete segments.  Raw prefix
    matching is intentionally unsupported: ``account/alice`` must not match
    ``account/alice-private``.
    """

    description: str
    effect: BoundaryEffect = BoundaryEffect.DENY
    capability: str = "*"
    scopes: tuple[str, ...] = ("**",)
    minimum_permission: PermissionLevel = PermissionLevel.OBSERVE
    model_visible: bool = True
    provenance: tuple[str, ...] = ()
    id: str = field(default_factory=lambda: gen_id("boundary"))


@dataclass(frozen=True)
class CredentialRef:
    """A lookup key for a credential provider, never credential material."""

    provider: str
    key: str

    def __post_init__(self) -> None:
        # Conservative identifiers keep common tokens, passwords and URLs out
        # of serialized policy state.  Actual secret bytes live in the named
        # provider (for example, a keychain or vault).
        identifier = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
        if not identifier.fullmatch(self.provider) or not identifier.fullmatch(self.key):
            raise ValueError(
                "credential references must be provider/key identifiers, not secret material"
            )


@dataclass(frozen=True)
class CapabilityGrant:
    capability: str
    permission: PermissionLevel
    scopes: tuple[str, ...]
    credential_ref: Optional[CredentialRef] = None
    granted_at: float = field(default_factory=now)
    expires_at: Optional[float] = None
    revoked_at: Optional[float] = None
    provenance: tuple[str, ...] = ()
    id: str = field(default_factory=lambda: gen_id("grant"))

    def __post_init__(self) -> None:
        if not self.capability.strip():
            raise ValueError("capability must not be empty")
        if not self.scopes:
            raise ValueError("a grant must declare at least one scope")
        for scope in self.scopes:
            _validate_scope_pattern(scope)

    def is_active(self, at: Optional[float] = None) -> bool:
        point = now() if at is None else at
        return (
            self.granted_at <= point
            and (self.expires_at is None or point < self.expires_at)
            and (self.revoked_at is None or point < self.revoked_at)
        )


@dataclass(frozen=True)
class ActionRequest:
    capability: str
    permission: PermissionLevel
    resource: str
    description: str = ""
    high_risk: bool = False
    external_write: bool = False
    requested_at: float = field(default_factory=now)
    id: str = field(default_factory=lambda: gen_id("action"))

    def to_dict(self) -> dict[str, Any]:
        return _dataclass_dict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ActionRequest":
        return cls(
            id=str(payload.get("id", gen_id("action"))),
            capability=str(payload["capability"]),
            permission=PermissionLevel(payload["permission"]),
            resource=str(payload["resource"]),
            description=str(payload.get("description", "")),
            high_risk=bool(payload.get("high_risk", False)),
            external_write=bool(payload.get("external_write", False)),
            requested_at=float(payload.get("requested_at", now())),
        )


@dataclass(frozen=True)
class ActionDecision:
    request_id: str
    status: DecisionStatus
    reason: str
    grant_id: Optional[str] = None
    policy_version: int = 0
    decided_at: float = field(default_factory=now)
    valid_until: Optional[float] = None
    confirmed_at: Optional[float] = None
    id: str = field(default_factory=lambda: gen_id("decision"))

    def __post_init__(self) -> None:
        if self.valid_until is not None and self.valid_until < self.decided_at:
            raise ValueError("authorization validity cannot end before it was decided")
        if self.confirmed_at is not None:
            if self.status is not DecisionStatus.ALLOWED:
                raise ValueError("only an allowed decision can carry owner confirmation")
            if self.confirmed_at < self.decided_at:
                raise ValueError("owner confirmation cannot predate the decision")
            if self.valid_until is not None and self.confirmed_at > self.valid_until:
                raise ValueError("owner confirmation cannot occur after authorization expiry")

    @property
    def allowed(self) -> bool:
        return self.status is DecisionStatus.ALLOWED

    @property
    def requires_human_confirmation(self) -> bool:
        return self.status is DecisionStatus.REQUIRES_CONFIRMATION

    def to_dict(self) -> dict[str, Any]:
        return _dataclass_dict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ActionDecision":
        grant_id = payload.get("grant_id")
        return cls(
            id=str(payload.get("id", gen_id("decision"))),
            request_id=str(payload["request_id"]),
            status=DecisionStatus(payload["status"]),
            reason=str(payload.get("reason", "")),
            grant_id=str(grant_id) if grant_id is not None else None,
            policy_version=int(payload.get("policy_version", 0)),
            decided_at=float(payload.get("decided_at", now())),
            valid_until=(
                float(payload["valid_until"])
                if payload.get("valid_until") is not None
                else None
            ),
            confirmed_at=(
                float(payload["confirmed_at"])
                if payload.get("confirmed_at") is not None
                else None
            ),
        )

    def is_fresh(self, at: Optional[float] = None) -> bool:
        point = now() if at is None else at
        return self.valid_until is not None and self.decided_at <= point <= self.valid_until


@dataclass(frozen=True)
class ActionRecord:
    """Audit record produced around execution; it performs no action itself."""

    request: ActionRequest
    decision: ActionDecision
    executed_at: Optional[float] = None
    outcome: str = ""
    provenance: tuple[str, ...] = ()
    id: str = field(default_factory=lambda: gen_id("action_record"))

    def __post_init__(self) -> None:
        if self.request.id != self.decision.request_id:
            raise ValueError("decision does not belong to this action request")
        if self.executed_at is not None and not self.decision.allowed:
            raise ValueError("a denied or unconfirmed action cannot be recorded as executed")
        if self.executed_at is not None and self.executed_at < self.decision.decided_at:
            raise ValueError("an action cannot be recorded before its authorization decision")
        if (
            self.executed_at is not None
            and self.decision.valid_until is not None
            and self.executed_at > self.decision.valid_until
        ):
            raise ValueError("an expired authorization cannot be recorded as executed")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "request": self.request.to_dict(),
            "decision": self.decision.to_dict(),
            "executed_at": self.executed_at,
            "outcome": self.outcome,
            "provenance": list(self.provenance),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ActionRecord":
        executed_at = payload.get("executed_at")
        return cls(
            id=str(payload.get("id", gen_id("action_record"))),
            request=ActionRequest.from_dict(payload["request"]),
            decision=ActionDecision.from_dict(payload["decision"]),
            executed_at=float(executed_at) if executed_at is not None else None,
            outcome=str(payload.get("outcome", "")),
            provenance=tuple(payload.get("provenance", ())),
        )


@dataclass(frozen=True)
class TwinContract:
    """Versioned owner intent and safety contract for a personal twin."""

    goals: tuple[Goal, ...] = ()
    principles: tuple[Principle, ...] = ()
    boundaries: tuple[Boundary, ...] = ()
    version: int = 1
    updated_at: float = field(default_factory=now)
    provenance: tuple[str, ...] = ()
    schema_version: int = 1
    confirm_high_risk_execution: bool = True
    confirm_external_writes: bool = True

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError(f"unsupported twin contract schema_version: {self.schema_version}")
        if self.version < 1:
            raise ValueError("contract version must be positive")
        if self.updated_at < 0:
            raise ValueError("updated_at must be non-negative")

    def update(
        self,
        *,
        goals: Optional[Iterable[Goal]] = None,
        principles: Optional[Iterable[Principle]] = None,
        boundaries: Optional[Iterable[Boundary]] = None,
        provenance: Optional[Iterable[str]] = None,
        updated_at: Optional[float] = None,
        confirm_high_risk_execution: Optional[bool] = None,
        confirm_external_writes: Optional[bool] = None,
    ) -> "TwinContract":
        """Return a new revision; prior contracts remain immutable audit evidence."""
        timestamp = now() if updated_at is None else updated_at
        if timestamp <= self.updated_at:
            timestamp = self.updated_at + 1e-6
        return replace(
            self,
            goals=self.goals if goals is None else tuple(goals),
            principles=self.principles if principles is None else tuple(principles),
            boundaries=self.boundaries if boundaries is None else tuple(boundaries),
            provenance=self.provenance if provenance is None else tuple(provenance),
            confirm_high_risk_execution=(
                self.confirm_high_risk_execution
                if confirm_high_risk_execution is None
                else confirm_high_risk_execution
            ),
            confirm_external_writes=(
                self.confirm_external_writes
                if confirm_external_writes is None
                else confirm_external_writes
            ),
            version=self.version + 1,
            updated_at=timestamp,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "version": self.version,
            "updated_at": self.updated_at,
            "provenance": list(self.provenance),
            "confirm_high_risk_execution": self.confirm_high_risk_execution,
            "confirm_external_writes": self.confirm_external_writes,
            "goals": [_dataclass_dict(item) for item in self.goals],
            "principles": [_dataclass_dict(item) for item in self.principles],
            "boundaries": [_dataclass_dict(item) for item in self.boundaries],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TwinContract":
        """Load schema v1 while tolerating absent and unknown additive fields."""
        schema_version = int(payload.get("schema_version", 1))
        boundaries = tuple(
            Boundary(
                id=item.get("id", gen_id("boundary")),
                description=str(item.get("description", "")),
                effect=BoundaryEffect(item.get("effect", BoundaryEffect.DENY.value)),
                capability=str(item.get("capability", "*")),
                scopes=tuple(item.get("scopes", ("**",))),
                minimum_permission=PermissionLevel(
                    item.get("minimum_permission", PermissionLevel.OBSERVE.value)
                ),
                model_visible=bool(item.get("model_visible", True)),
                provenance=tuple(item.get("provenance", ())),
            )
            for item in payload.get("boundaries", ())
        )
        return cls(
            schema_version=schema_version,
            version=int(payload.get("version", 1)),
            updated_at=float(payload.get("updated_at", 0.0)),
            provenance=tuple(payload.get("provenance", ())),
            confirm_high_risk_execution=bool(payload.get("confirm_high_risk_execution", True)),
            confirm_external_writes=bool(payload.get("confirm_external_writes", True)),
            goals=tuple(_goal_from_dict(item) for item in payload.get("goals", ())),
            principles=tuple(_principle_from_dict(item) for item in payload.get("principles", ())),
            boundaries=boundaries,
        )

    def to_model_context(self) -> dict[str, Any]:
        """Return only owner-approved semantic guidance for model prompting.

        Capability grants, credential references, scope patterns and policy
        effects are intentionally absent.  Those controls belong to the trusted
        executor, not to prompt-visible context.
        """
        return {
            "contract_version": self.version,
            "goals": [
                {"title": item.title, "description": item.description, "status": item.status}
                for item in self.goals
            ],
            "principles": [
                {"name": item.name, "statement": item.statement} for item in self.principles
            ],
            "boundaries": [
                {"description": item.description}
                for item in self.boundaries
                if item.model_visible
            ],
        }


@dataclass
class CapabilityRegistry:
    """Owner-controlled grants and deterministic default-deny evaluation."""

    grants: list[CapabilityGrant] = field(default_factory=list)

    def add(self, grant: CapabilityGrant) -> None:
        if any(existing.id == grant.id for existing in self.grants):
            raise ValueError(f"duplicate grant id: {grant.id}")
        self.grants.append(grant)

    def revoke(self, grant_id: str, *, at: Optional[float] = None) -> CapabilityGrant:
        revoked_at = now() if at is None else at
        for index, grant in enumerate(self.grants):
            if grant.id == grant_id:
                replacement = replace(grant, revoked_at=revoked_at)
                self.grants[index] = replacement
                return replacement
        raise KeyError(grant_id)

    def decide(
        self,
        request: ActionRequest,
        contract: TwinContract,
        *,
        human_confirmed: bool = False,
        at: Optional[float] = None,
    ) -> ActionDecision:
        point = now() if at is None else at
        try:
            resource_segments = _scope_segments(request.resource)
            if any("*" in segment for segment in resource_segments):
                raise ValueError("resource must not contain wildcards")
        except ValueError:
            # An executor must canonicalize once, before asking for authority.
            # Malformed or ambiguous resource identifiers fail closed.
            return _decision(
                request,
                contract,
                DecisionStatus.DENIED,
                "resource is not canonical",
                decided_at=point,
                valid_until=point,
            )
        matching = [
            grant
            for grant in self.grants
            if grant.is_active(point)
            and grant.capability == request.capability
            and grant.permission.includes(request.permission)
            and any(scope_matches(scope, request.resource) for scope in grant.scopes)
        ]
        if not matching:
            return _decision(
                request,
                contract,
                DecisionStatus.DENIED,
                "no active matching grant",
                decided_at=point,
                valid_until=point,
            )

        # A boundary can only remove authority or require confirmation; it can
        # never create authority that a capability grant did not provide.
        applicable = [
            boundary
            for boundary in contract.boundaries
            if boundary.capability in ("*", request.capability)
            and request.permission.rank >= boundary.minimum_permission.rank
            and any(scope_matches(scope, request.resource) for scope in boundary.scopes)
        ]
        denying = next((b for b in applicable if b.effect is BoundaryEffect.DENY), None)
        chosen = max(matching, key=lambda grant: grant.permission.rank)
        valid_until = point + DECISION_TTL_SECONDS
        if chosen.expires_at is not None:
            valid_until = min(valid_until, chosen.expires_at)
        if denying is not None:
            return _decision(
                request,
                contract,
                DecisionStatus.DENIED,
                "blocked by owner boundary",
                chosen.id,
                decided_at=point,
                valid_until=point,
            )

        needs_confirmation = any(
            boundary.effect is BoundaryEffect.REQUIRE_CONFIRMATION for boundary in applicable
        )
        if request.permission is PermissionLevel.EXECUTE:
            needs_confirmation = needs_confirmation or (
                request.high_risk and contract.confirm_high_risk_execution
            )
            needs_confirmation = needs_confirmation or (
                request.external_write and contract.confirm_external_writes
            )
        if needs_confirmation and not human_confirmed:
            return _decision(
                request,
                contract,
                DecisionStatus.REQUIRES_CONFIRMATION,
                "owner confirmation required",
                chosen.id,
                decided_at=point,
                valid_until=valid_until,
            )
        return _decision(
            request,
            contract,
            DecisionStatus.ALLOWED,
            "authorized",
            chosen.id,
            decided_at=point,
            valid_until=valid_until,
            confirmed_at=point if human_confirmed else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": 1, "grants": [_dataclass_dict(grant) for grant in self.grants]}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CapabilityRegistry":
        if int(payload.get("schema_version", 1)) != 1:
            raise ValueError("unsupported capability registry schema_version")
        grants = []
        for item in payload.get("grants", ()):
            credential = item.get("credential_ref")
            grants.append(
                CapabilityGrant(
                    id=item.get("id", gen_id("grant")),
                    capability=str(item["capability"]),
                    permission=PermissionLevel(item["permission"]),
                    scopes=tuple(item["scopes"]),
                    credential_ref=CredentialRef(**credential) if credential else None,
                    granted_at=float(item.get("granted_at", 0.0)),
                    expires_at=item.get("expires_at"),
                    revoked_at=item.get("revoked_at"),
                    provenance=tuple(item.get("provenance", ())),
                )
            )
        return cls(grants=grants)


def _scope_segments(value: str) -> tuple[str, ...]:
    encoded_octet = re.search(r"%[0-9a-f]{2}", value, flags=re.IGNORECASE)
    if (
        not value
        or value.startswith("/")
        or value.endswith("/")
        or "//" in value
        or "\\" in value
        or encoded_octet is not None
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError("scope must be a non-empty canonical relative path")
    segments = tuple(value.split("/"))
    if any(segment in (".", "..") for segment in segments):
        raise ValueError("scope must not contain dot segments")
    return segments


def _validate_scope_pattern(pattern: str) -> None:
    segments = _scope_segments(pattern)
    for index, segment in enumerate(segments):
        if "*" in segment and segment not in ("*", "**"):
            raise ValueError("wildcards must occupy a complete scope segment")
        if segment == "**" and index != len(segments) - 1:
            raise ValueError("** is only supported as the final scope segment")


def scope_matches(pattern: str, resource: str) -> bool:
    """Match complete path segments, never raw string prefixes."""
    _validate_scope_pattern(pattern)
    expected = _scope_segments(pattern)
    actual = _scope_segments(resource)
    if any("*" in segment for segment in actual):
        raise ValueError("resource must not contain wildcards")
    for index, segment in enumerate(expected):
        if segment == "**":
            return True  # all preceding complete segments have already matched
        if index >= len(actual) or (segment != "*" and segment != actual[index]):
            return False
    return len(actual) == len(expected)


def _dataclass_dict(item: Any) -> dict[str, Any]:
    payload = asdict(item)
    for key, value in tuple(payload.items()):
        if isinstance(value, Enum):
            payload[key] = value.value
        elif isinstance(value, tuple):
            payload[key] = list(value)
    return payload


def _goal_from_dict(item: Mapping[str, Any]) -> Goal:
    return Goal(
        id=item.get("id", gen_id("goal")),
        title=str(item["title"]),
        description=str(item.get("description", "")),
        status=str(item.get("status", "active")),
        priority=int(item.get("priority", 0)),
        provenance=tuple(item.get("provenance", ())),
    )


def _principle_from_dict(item: Mapping[str, Any]) -> Principle:
    return Principle(
        id=item.get("id", gen_id("principle")),
        statement=str(item["statement"]),
        name=str(item.get("name", "")),
        priority=int(item.get("priority", 0)),
        provenance=tuple(item.get("provenance", ())),
    )


def _decision(
    request: ActionRequest,
    contract: TwinContract,
    status: DecisionStatus,
    reason: str,
    grant_id: Optional[str] = None,
    *,
    decided_at: Optional[float] = None,
    valid_until: Optional[float] = None,
    confirmed_at: Optional[float] = None,
) -> ActionDecision:
    return ActionDecision(
        request_id=request.id,
        status=status,
        reason=reason,
        grant_id=grant_id,
        policy_version=contract.version,
        decided_at=now() if decided_at is None else decided_at,
        valid_until=valid_until,
        confirmed_at=confirmed_at,
    )
