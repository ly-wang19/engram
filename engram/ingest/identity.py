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
        while self._parent[root] != root:
            root = self._parent[root]
        # path compression
        while self._parent[x] != root:
            self._parent[x], x = root, self._parent[x]
        return root

    def resolve(self, user_id: str) -> str:
        """Return the canonical id for a handle (the handle itself until it is linked)."""
        return self._find(user_id)

    def link(self, a: str, b: str) -> str:
        """Declare two handles to be the same person; returns the canonical id."""
        ra, rb = self._find(a), self._find(b)
        if ra != rb:
            # keep the lexicographically smaller root as canonical for determinism
            root, child = sorted((ra, rb))
            self._parent[child] = root
        return self._find(a)
