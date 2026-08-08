"""Identity resolution -- a first-class primitive, not an afterthought.

Stable `user_id` is the assumption every competitor quietly makes and then breaks on multi-device /
anonymous sessions (an explicitly-admitted open gap in the field; CLAUDE.md §1.4). We model it as a
union-find over aliases so two handles ("u1", "wei@example.com") can be merged into one canonical
identity without rewriting history.
"""
from __future__ import annotations


class IdentityResolver:
    def __init__(self) -> None:
        self._parent: dict[str, str] = {}

    def _find(self, x: str) -> str:
        self._parent.setdefault(x, x)
        root = x
        while self._parent.setdefault(root, root) != root:
            root = self._parent[root]
        # path compression
        while self._parent[x] != root:
            self._parent[x], x = root, self._parent[x]
        return root

    def resolve(self, user_id: str) -> str:
        """Return the canonical id for a handle (the handle itself until it is linked)."""
        return self._find(user_id)

    def component(self, user_id: str) -> frozenset[str]:
        """Return every known handle linked to ``user_id``, including its canonical id.

        The resolver persists only ``_parent`` for backwards compatibility. Deriving membership from
        that map keeps the component authoritative after loading old snapshots and avoids a second
        alias index that could drift out of sync.
        """
        root = self._find(user_id)
        return frozenset(handle for handle in tuple(self._parent) if self._find(handle) == root)

    def components(self) -> tuple[frozenset[str], ...]:
        """Return all known identity components in deterministic canonical-id order."""
        grouped: dict[str, set[str]] = {}
        for handle in tuple(self._parent):
            grouped.setdefault(self._find(handle), set()).add(handle)
        return tuple(frozenset(grouped[root]) for root in sorted(grouped))

    def link(self, a: str, b: str) -> str:
        """Declare two handles to be the same person; returns the canonical id."""
        ra, rb = self._find(a), self._find(b)
        if ra != rb:
            # keep the lexicographically smaller root as canonical for determinism
            root, child = sorted((ra, rb))
            self._parent[child] = root
        canonical = self._find(a)
        # A compact parent map makes serialization stable regardless of link declaration order.
        for handle in self.component(canonical):
            self._parent[handle] = canonical
        return canonical
