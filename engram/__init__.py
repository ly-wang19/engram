"""Engram — an open-source long-term memory engine. See CLAUDE.md for the project charter.

Public API is finalized in this module once the Memory facade is wired (see engram/memory.py).
"""

__version__ = "0.1.0"

from .config import Config
from .twin import (
    ActionDecision,
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
)
from .types import Entity, Episode, Fact, Relation

__all__ = [
    "__version__",
    "Config",
    "Episode",
    "Fact",
    "Entity",
    "Relation",
    "ActionDecision",
    "ActionRecord",
    "ActionRequest",
    "Boundary",
    "BoundaryEffect",
    "CapabilityGrant",
    "CapabilityRegistry",
    "CredentialRef",
    "DecisionStatus",
    "Goal",
    "PermissionLevel",
    "Principle",
    "TwinContract",
]

# Memory facade is attached lazily to avoid import-time cost; see engram/memory.py.
try:  # pragma: no cover - exercised once memory.py lands
    from .memory import Memory  # noqa: F401

    __all__.append("Memory")
except Exception:  # noqa: BLE001 - partial-build tolerance during development
    pass
