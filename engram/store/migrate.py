"""Explicit one-shot migration from legacy pickle snapshots to JSONL stores.

Normal `Memory.open()` does not read pickle anymore. This module is the quarantined compatibility path:
use it only for trusted legacy snapshots that you intentionally want to migrate.
"""
from __future__ import annotations

import argparse
import json
import pickle
from typing import Any

from ..memory import Memory
from .persist import save_memory


def _apply_legacy_blob(mem: Memory, blob: Any) -> None:
    if isinstance(blob, dict):
        mem.episodes_doc = blob["episodes_doc"]
        mem.episodes_vec = blob["episodes_vec"]
        mem.fact_store = blob["fact_store"]
        mem.cold_store = blob["cold_store"]
        mem.summary_vec = blob["summary_vec"]
        mem.graph = blob["graph"]
        mem.resolver = blob["resolver"]
        mem._persona_cache = blob.get("persona_cache") or {}
        mem.focus = blob.get("focus") or {"track": [], "mute": []}
        mem.policy = blob.get("policy") or {
            "extract_instruction": "",
            "extract_system": "",
            "summary_system": "",
            "persona_system": "",
        }
        mem.working_mem = blob.get("working_mem") or {}
        mem._identity = blob.get("identity") or {}
        mem._aliases = {k: set(v) for k, v in (blob.get("aliases") or {}).items()}
        mem.conflicts = blob.get("conflicts") or {}
        return
    (
        mem.episodes_doc,
        mem.episodes_vec,
        mem.fact_store,
        mem.cold_store,
        mem.summary_vec,
        mem.graph,
        mem.resolver,
        mem._persona_cache,
    ) = blob


def load_legacy_pickle(path: str) -> Memory:
    with open(path, "rb") as fh:
        blob = pickle.load(fh)  # noqa: S301 - explicit trusted legacy migration command.
    mem = Memory()
    _apply_legacy_blob(mem, blob)
    mem._rewire()
    mem._classify()
    return mem


def counts(mem: Memory) -> dict[str, int]:
    return {
        "episodes": len(mem.episodes_doc.values()),
        "facts": len(mem.fact_store.values()) + len(mem.cold_store.values()),
        "entities": len(getattr(mem.graph, "entities", {})),
        "relations": len(mem.graph.relations()),
        "working": len(mem.working_mem),
        "conflicts": len(mem.conflicts),
    }


def migrate(from_path: str, to_path: str, dry_run: bool = False) -> dict[str, Any]:
    mem = load_legacy_pickle(from_path)
    out = {"ok": True, "from": from_path, "to": to_path, "dry_run": dry_run, "counts": counts(mem)}
    if not dry_run:
        save_memory(mem, to_path)
        out["written"] = True
    else:
        out["written"] = False
    return out


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="python -m engram.store.migrate",
        description="Migrate a trusted legacy Engram pickle snapshot to the safe JSONL+manifest format.",
    )
    ap.add_argument("--from", dest="from_path", required=True, help="legacy .pkl snapshot path")
    ap.add_argument("--to", dest="to_path", required=True, help="target JSONL store directory")
    ap.add_argument("--dry-run", action="store_true", help="report counts without writing")
    args = ap.parse_args()
    print(json.dumps(migrate(args.from_path, args.to_path, args.dry_run), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
