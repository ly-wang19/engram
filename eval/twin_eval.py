"""Deterministic, zero-dependency validation harness for personal-twin governance.

This is an invariant suite, not a model-quality benchmark and not evidence for Engram's public
LongMemEval claims.  Every row is one traceable safety assertion with a stable scenario id.  The final
summary exposes the exact overall and per-category denominators plus the complete failure list.

Run it from the repository root:

    python eval/twin_eval.py
    python eval/twin_eval.py --out /tmp/engram-twin-eval.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engram.service import MemoryService  # noqa: E402


HARNESS = "personal_twin_governance"
SCHEMA_VERSION = 1


def _result(
    scenario_id: str,
    category: str,
    description: str,
    expected: Any,
    observed: Any,
) -> dict[str, Any]:
    return {
        "type": "scenario",
        "harness": HARNESS,
        "schema_version": SCHEMA_VERSION,
        "id": scenario_id,
        "category": category,
        "description": description,
        "expected": expected,
        "observed": observed,
        "ok": observed == expected,
    }


def _service(data_dir: str) -> MemoryService:
    return MemoryService(data_dir=data_dir, embedder_name="hashing", llm_name="")


def _authorization_scenarios(service: MemoryService) -> list[dict[str, Any]]:
    rows = []
    denied = service.authorize_twin_action(
        "eval-default-deny",
        capability="calendar",
        permission="observe",
        resource="calendars/personal/events",
    )
    rows.append(
        _result(
            "auth.default_deny",
            "authorization",
            "A namespace without a grant cannot observe a resource.",
            "denied",
            denied["decision"]["status"],
        )
    )

    user = "eval-scope"
    service.grant_capability(
        user,
        capability="calendar",
        permission="execute",
        scopes=["calendars/personal/**"],
        provenance=["synthetic:owner-grant"],
    )
    exact = service.authorize_twin_action(
        user,
        capability="calendar",
        permission="observe",
        resource="calendars/personal/events/42",
    )
    rows.append(
        _result(
            "scope.segment_match",
            "scope",
            "A canonical child resource matches a segment-scoped grant.",
            "allowed",
            exact["decision"]["status"],
        )
    )

    bypasses = (
        (
            "scope.prefix_bypass",
            "calendars/personal-private/events/42",
            "A raw string-prefix lookalike must not match a segment.",
        ),
        (
            "scope.dotdot_bypass",
            "calendars/personal/../admin",
            "A dot-segment traversal resource must fail closed.",
        ),
        (
            "scope.encoded_bypass",
            "calendars/personal%2f..%2fadmin",
            "A percent-encoded separator traversal must fail closed.",
        ),
        (
            "scope.backslash_bypass",
            "calendars\\personal\\admin",
            "A backslash-delimited resource must fail closed.",
        ),
        (
            "scope.wildcard_bypass",
            "calendars/*/events/42",
            "A caller-controlled wildcard resource must fail closed.",
        ),
    )
    for scenario_id, resource, description in bypasses:
        decision = service.authorize_twin_action(
            user,
            capability="calendar",
            permission="observe",
            resource=resource,
        )
        rows.append(
            _result(
                scenario_id,
                "scope",
                description,
                "denied",
                decision["decision"]["status"],
            )
        )
    return rows


def _confirmation_scenarios(service: MemoryService) -> list[dict[str, Any]]:
    user = "eval-confirmation"
    service.grant_capability(
        user,
        capability="mail",
        permission="execute",
        scopes=["mailboxes/personal/**"],
        provenance=["synthetic:owner-grant"],
    )
    pending = service.authorize_twin_action(
        user,
        capability="mail",
        permission="execute",
        resource="mailboxes/personal/messages/42",
        external_write=True,
    )
    allowed = service.confirm_twin_action(user, pending["decision"]["id"])
    return [
        _result(
            "confirmation.external_write_gate",
            "confirmation",
            "An external write pauses for owner confirmation.",
            "requires_confirmation",
            pending["decision"]["status"],
        ),
        _result(
            "confirmation.owner_confirmed",
            "confirmation",
            "The same in-scope write may proceed only through the separate owner control plane.",
            "allowed",
            allowed["decision"]["status"],
        ),
        _result(
            "confirmation.authorization_never_executes",
            "confirmation",
            "The authorization surface reports policy only and never executes the action.",
            False,
            allowed["executed"],
        ),
    ]


def _lifecycle_scenarios(service: MemoryService) -> list[dict[str, Any]]:
    expired_user = "eval-expired"
    service.grant_capability(
        expired_user,
        capability="files",
        permission="observe",
        scopes=["documents/personal/**"],
        expires_at=0.0,
        provenance=["synthetic:expired-grant"],
    )
    expired = service.authorize_twin_action(
        expired_user,
        capability="files",
        permission="observe",
        resource="documents/personal/note.txt",
    )

    revoked_user = "eval-revoked"
    grant = service.grant_capability(
        revoked_user,
        capability="files",
        permission="observe",
        scopes=["documents/personal/**"],
        provenance=["synthetic:revocable-grant"],
    )["grant"]
    service.revoke_capability(revoked_user, grant["id"])
    revoked = service.authorize_twin_action(
        revoked_user,
        capability="files",
        permission="observe",
        resource="documents/personal/note.txt",
    )
    return [
        _result(
            "grant.expired_deny",
            "grant_lifecycle",
            "An expired grant confers no authority.",
            "denied",
            expired["decision"]["status"],
        ),
        _result(
            "grant.revoked_deny",
            "grant_lifecycle",
            "A revoked grant confers no authority.",
            "denied",
            revoked["decision"]["status"],
        ),
    ]


def _contract_scenario(service: MemoryService) -> dict[str, Any]:
    user = "eval-contract"
    before = service.twin_contract(user)["contract"]
    revised = service.revise_twin_contract(
        user,
        {
            "goals": [
                {
                    "title": "Protect focused work",
                    "description": "Keep synthetic mornings meeting-free",
                    "provenance": ["synthetic:owner-revision"],
                }
            ],
            "provenance": ["synthetic:contract-v2"],
        },
    )["contract"]
    service.grant_capability(
        user,
        capability="calendar",
        permission="observe",
        scopes=["calendars/personal/**"],
    )
    decision = service.authorize_twin_action(
        user,
        capability="calendar",
        permission="observe",
        resource="calendars/personal/events",
    )
    observed = {
        "before": before["version"],
        "after": revised["version"],
        "decision_policy_version": decision["decision"]["policy_version"],
        "prior_snapshot_unchanged": before["version"] == 1,
    }
    return _result(
        "contract.versioned_revision",
        "contract",
        "A contract edit creates v+1 and every decision records the governing version.",
        {
            "before": 1,
            "after": 2,
            "decision_policy_version": 2,
            "prior_snapshot_unchanged": True,
        },
        observed,
    )


def _persistence_scenario(data_dir: str, service: MemoryService) -> dict[str, Any]:
    user = "eval-audit"
    service.grant_capability(
        user,
        capability="files",
        permission="execute",
        scopes=["documents/personal/**"],
        provenance=["synthetic:owner-grant"],
    )
    authorized = service.authorize_twin_action(
        user,
        capability="files",
        permission="execute",
        resource="documents/personal/report.txt",
        description="Write a synthetic report",
    )
    decision_id = authorized["decision"]["id"]
    service.record_twin_action(
        user,
        decision_id,
        "synthetic write completed",
        executed_at=authorized["decision"]["decided_at"],
        provenance=["synthetic:executor-receipt"],
    )

    reloaded = _service(data_dir).get(user)
    request, decision = reloaded.twin_decisions[decision_id]
    matching_actions = [
        item for item in reloaded.twin_actions if item.decision.id == decision_id
    ]
    action = matching_actions[0] if matching_actions else None
    observed = {
        "decision_present": decision.request_id == request.id,
        "decision_status": decision.status.value,
        "action_count": len(matching_actions),
        "outcome": action.outcome if action else None,
        "provenance_preserved": (
            tuple(action.provenance) == ("synthetic:executor-receipt",) if action else False
        ),
    }
    return _result(
        "audit.persistence_roundtrip",
        "audit",
        "The authorization decision and executor outcome survive a fresh durable open.",
        {
            "decision_present": True,
            "decision_status": "allowed",
            "action_count": 1,
            "outcome": "synthetic write completed",
            "provenance_preserved": True,
        },
        observed,
    )


def _erasure_scenarios(data_dir: str, service: MemoryService) -> list[dict[str, Any]]:
    user = "eval-erasure"
    mem = service.get(user)
    source = mem.add(
        "Synthetic private source to erase.",
        user_id=user,
        session_id="erase-source",
    )
    target = mem.add_fact("user", "synthetic_secret", "alpha", user_id=user)
    sibling = mem.add_fact("user", "synthetic_detail", "beta", user_id=user)
    for fact in (target, sibling):
        fact.provenance = [source.id]
        mem._upsert_fact(fact)

    kept_source = mem.add(
        "Synthetic source that must remain.",
        user_id=user,
        session_id="keep-source",
    )
    kept_fact = mem.add_fact("user", "synthetic_public", "gamma", user_id=user)
    kept_fact.provenance = [kept_source.id]
    mem._upsert_fact(kept_fact)
    service._save(user, mem)

    erased = service.delete_fact(user, target.id, confirm=True)
    reloaded = _service(data_dir).get(user)
    remaining_facts = reloaded.fact_store.values() + reloaded.cold_store.values()
    receipt = erased.get("erasure", {})
    cascade_observed = {
        "request_ok": erased.get("ok", False),
        "verified": receipt.get("verified", False),
        "storage_verified": receipt.get("storage_verified", False),
        "facts_removed": receipt.get("counts", {}).get("facts"),
        "episodes_removed": receipt.get("counts", {}).get("episodes"),
        "target_absent": reloaded.fact_store.get(target.id) is None,
        "sibling_absent": reloaded.fact_store.get(sibling.id) is None,
        "source_absent": reloaded.episodes_doc.get(source.id) is None,
        "no_dangling_provenance": not any(
            source.id in fact.provenance for fact in remaining_facts
        ),
    }
    preserve_observed = {
        "fact_present": reloaded.fact_store.get(kept_fact.id) is not None,
        "source_present": reloaded.episodes_doc.get(kept_source.id) is not None,
    }
    return [
        _result(
            "erasure.provenance_cascade",
            "erasure",
            "Erasing a derived fact removes its source, sibling derivations, and dangling provenance.",
            {
                "request_ok": True,
                "verified": True,
                "storage_verified": True,
                "facts_removed": 2,
                "episodes_removed": 1,
                "target_absent": True,
                "sibling_absent": True,
                "source_absent": True,
                "no_dangling_provenance": True,
            },
            cascade_observed,
        ),
        _result(
            "erasure.unrelated_source_preserved",
            "erasure",
            "Provenance erasure does not remove an unrelated source and its fact.",
            {"fact_present": True, "source_present": True},
            preserve_observed,
        ),
    ]


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    categories: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        categories[row["category"]].append(row)

    per_category = {}
    for category in sorted(categories):
        items = categories[category]
        passed = sum(bool(item["ok"]) for item in items)
        per_category[category] = {
            "passed": passed,
            "total": len(items),
            "pass_rate": 100.0 * passed / len(items),
            "scenario_ids": [item["id"] for item in items],
        }

    failures = [
        {
            "id": row["id"],
            "category": row["category"],
            "expected": row["expected"],
            "observed": row["observed"],
        }
        for row in rows
        if not row["ok"]
    ]
    passed = len(rows) - len(failures)
    return {
        "type": "summary",
        "harness": HARNESS,
        "schema_version": SCHEMA_VERSION,
        "evidence_scope": "offline deterministic invariant validation; not public benchmark evidence",
        "passed": passed,
        "total": len(rows),
        "pass_rate": 100.0 * passed / len(rows) if rows else 0.0,
        "scenario_ids": [row["id"] for row in rows],
        "per_category": per_category,
        "failures": failures,
    }


def evaluate() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run every governance scenario in isolated temporary durable storage."""
    with tempfile.TemporaryDirectory(prefix="engram_twin_eval_") as data_dir:
        service = _service(data_dir)
        rows = _authorization_scenarios(service)
        rows.extend(_confirmation_scenarios(service))
        rows.extend(_lifecycle_scenarios(service))
        rows.append(_contract_scenario(service))
        rows.append(_persistence_scenario(data_dir, service))
        rows.extend(_erasure_scenarios(data_dir, service))
        return rows, _summarize(rows)


def write_jsonl(path: str, rows: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as handle:
        for record in [*rows, summary]:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Engram's deterministic offline personal-twin governance validation."
    )
    parser.add_argument("--out", help="write scenario rows plus summary as JSONL")
    args = parser.parse_args()

    rows, summary = evaluate()
    print(f"Personal-twin governance eval -- {summary['total']} deterministic offline scenarios")
    print("  id                                      category          result")
    print("  " + "-" * 72)
    for row in rows:
        print(
            f"  {row['id']:<39} {row['category']:<17} "
            f"{'PASS' if row['ok'] else 'FAIL'}"
        )
    print(
        f"\n  Overall: {summary['passed']}/{summary['total']} "
        f"({summary['pass_rate']:.1f}%)"
    )
    if summary["failures"]:
        print("  Failures: " + ", ".join(item["id"] for item in summary["failures"]))
    else:
        print("  Failures: none")
    print("  Scope: offline invariant validation; not public benchmark evidence")
    if args.out:
        write_jsonl(args.out, rows, summary)
        print(f"  Raw log: {args.out}")
    if summary["failures"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
