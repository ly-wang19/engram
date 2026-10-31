"""Bounded, versioned, restartable erasure of a tenant's selected memory and its derivatives."""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import secrets
import time
from dataclasses import asdict


class ForgetError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def normalize_scope(scope: dict) -> dict:
    allowed = {"session_ids", "source", "time_from", "time_to"}
    if not isinstance(scope, dict) or set(scope) - allowed:
        raise ForgetError("unknown forget scope fields")
    sessions = scope.get("session_ids") or []
    if (not isinstance(sessions, list) or len(sessions) > 100
            or any(not isinstance(s, str) or not s.strip() or len(s) > 512 for s in sessions)):
        raise ForgetError("session_ids must contain 1–100 nonempty exact session identifiers")
    source = scope.get("source")
    if source is not None and (not isinstance(source, str) or not source.strip() or len(source) > 200):
        raise ForgetError("source must be a nonempty exact source name")
    start, end = scope.get("time_from"), scope.get("time_to")
    if (start is None) != (end is None):
        raise ForgetError("a time range requires both time_from and time_to")
    if start is not None:
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
               for v in (start, end)) or start >= end:
            raise ForgetError("time range must be finite with time_from < time_to")
    if not sessions and not source and start is None:
        raise ForgetError("an empty or unbounded scope is not allowed")
    return {"session_ids": sorted(set(sessions)), "source": source,
            "time_from": start, "time_to": end}


class SelectiveForgetting:
    preview_ttl = 900

    def __init__(self, service):
        self.service = service
        self.store = service.management_store

    @staticmethod
    def _owner(mem, obj, user):
        return obj.user_id == mem.resolver.resolve(user)

    @staticmethod
    def _matches(obj, scope):
        if scope["session_ids"] and obj.session_id not in scope["session_ids"]:
            return False
        if scope["source"] is not None and obj.metadata.get("source") != scope["source"]:
            return False
        return scope["time_from"] is None or scope["time_from"] <= obj.event_time < scope["time_to"]

    def _version(self, mem, user):
        def records(items):
            rows = []
            for item in items:
                if not self._owner(mem, item, user):
                    continue
                row = asdict(item)
                for key in ("embedding", "summary_embedding", "last_access", "access_count", "salience"):
                    row.pop(key, None)
                rows.append(row)
            return sorted(rows, key=lambda r: r["id"])
        return _digest({"episodes": records(mem.episodes_doc.values()),
                        "facts": records(mem._all_facts()), "working": records(mem.working_mem.values()),
                        "conflicts": records(mem.conflicts.values()),
                        # Review notes/history are user data too; include them in the approval version.
                        "review": [self.store.list(user, name) for name in ("review_questions", "review_health")],
                        "connection_events": self.store.list(user, "connection_events")})

    def _plan(self, mem, user, scope):
        episodes = [e for e in mem.episodes_doc.values() if self._owner(mem, e, user)]
        facts = [f for f in mem._all_facts() if self._owner(mem, f, user)]
        ep_ids = {e.id for e in episodes if self._matches(e, scope)}
        sessions = {e.session_id for e in episodes if e.id in ep_ids}
        if scope["session_ids"] and scope["source"] is None and scope["time_from"] is None:
            sessions.update(scope["session_ids"])
        fact_ids = set()
        # Source-less manually authored facts can be selected by a bounded time-only range.
        if not scope["session_ids"] and scope["source"] is None and scope["time_from"] is not None:
            fact_ids.update(f.id for f in facts if not f.provenance
                            and scope["time_from"] <= f.valid_at < scope["time_to"])
        working = [w for w in mem.working_mem.values() if self._owner(mem, w, user)
                   and (self._matches(w, scope) or w.session_id in sessions)]
        sessions.update(w.session_id for w in working)
        if not ep_ids and not fact_ids and not working:
            raise ForgetError("scope matches no memory", 404)
        # Historical extractions without lineage cannot be proven independent. Include them explicitly
        # rather than silently leaving a potentially-derived private claim recallable.
        untracked = {f.id for f in facts if not f.provenance and f.source != "user"}
        fact_ids |= untracked
        changed = True
        while changed:
            old = len(fact_ids)
            known = ep_ids | fact_ids
            fact_ids.update(f.id for f in facts if set(f.provenance) & known)
            changed = len(fact_ids) != old
        shared = [f.id for f in facts if f.id in fact_ids and set(f.provenance) & ep_ids
                  and set(f.provenance) - ep_ids - fact_ids]
        # Reflection can copy a later fact into an older, unrelated episode's summary. There is no
        # complete summary lineage, so clear all tenant summaries and rebuild lazily from retained raw.
        summaries = {e.id for e in episodes if e.summary or e.summary_embedding}
        summaries.update(e.id for e in mem.summary_vec.values() if self._owner(mem, e, user))
        conflicts = [c.id for c in mem.conflicts.values() if self._owner(mem, c, user)
                     and ({c.older, c.newer} & fact_ids)]
        events = [event for event in self.store.list(user, "connection_events")
                  if event.get("session_id") in sessions
                  or (not event.get("session_id") and scope["source"] is not None
                      and event.get("source") == scope["source"]
                      and (scope["time_from"] is None
                           or scope["time_from"] <= event.get("at", 0) < scope["time_to"]))]
        plan = {"connection_ids": [event["id"] for event in events],
                "connection_sources": sorted({event["source"] for event in events}),
                "episode_ids": sorted(ep_ids), "fact_ids": sorted(fact_ids),
                "session_ids": sorted(sessions), "working_ids": sorted(w.id for w in working),
                "summary_ids": sorted(summaries), "conflict_ids": sorted(conflicts),
                "review_ids": {name: [r["id"] for r in self.store.list(user, name)]
                               for name in ("review_questions", "review_health")}}
        plan["counts"] = {"episodes": len(ep_ids), "facts": len(fact_ids),
                          "facts_hot": sum(f.id in fact_ids for f in mem.fact_store.values()),
                          "facts_cold": sum(f.id in fact_ids for f in mem.cold_store.values()),
                          "shared_source_facts": len(shared), "untracked_derived_facts": len(untracked),
                          "summaries": len(summaries), "working": len(working),
                          "relations": sum(r.fact_id in fact_ids for r in mem.graph.relations()),
                          "conflicts": len(conflicts),
                          "review_records": sum(map(len, plan["review_ids"].values())),
                          "connection_events": len(events)}
        return plan

    def _record(self, user, token):
        if not isinstance(token, str) or not 20 <= len(token) <= 200:
            raise ForgetError("invalid preview token", 409)
        row = self.store.get(user, "forget_previews", _digest(token))
        if not row or row["status"] != "preview" or row["expires_at"] <= time.time():
            raise ForgetError("preview expired, consumed, or belongs to another namespace", 409)
        return row

    def preview(self, user, scope, *, include_sensitive=False):
        scope = normalize_scope(scope)
        with self.service.write_lock(user):
            mem = self.service.get(user)
            self.recover_locked(user, mem)
            plan = self._plan(mem, user, scope)
            version, token = self._version(mem, user), secrets.token_urlsafe(32)
            row = {"id": _digest(token), "scope": scope, "version": version, "plan": plan,
                   "created_at": time.time(), "expires_at": time.time() + self.preview_ttl,
                   "status": "preview"}
            self.store.put(user, "forget_previews", row["id"], row)
            # The DB holds IDs/counts, never the raw preview content.
            return {"token": token, "scope": scope, "version": version,
                    "expires_at": row["expires_at"], "counts": plan["counts"],
                    "episodes": self._items(mem, plan, "episodes", include_sensitive, 0, 20),
                    "facts": self._items(mem, plan, "facts", include_sensitive, 0, 20),
                    "policy": "delete_any_dependent_fact_clear_all_summaries_and_review_state",
                    "include_sensitive": include_sensitive}

    @staticmethod
    def _items(mem, plan, kind, sensitive, offset, limit):
        ids = plan["episode_ids" if kind == "episodes" else "fact_ids"]
        rows = []
        for ident in ids[offset:offset + limit]:
            obj = (mem.episodes_doc.get(ident) if kind == "episodes"
                   else mem.fact_store.get(ident) or mem.cold_store.get(ident))
            if obj is None:
                continue
            if kind == "episodes":
                rows.append({"id": ident, "session_id": obj.session_id,
                             "event_time": obj.event_time, "source": obj.metadata.get("source", ""),
                             "text": obj.content[:1000] if sensitive else "", "redacted": not sensitive})
            else:
                hidden = obj.sensitive and not sensitive
                rows.append({"id": ident, "text": "" if hidden else obj.text,
                             "source": obj.source, "redacted": hidden})
        return {"items": rows, "total": len(ids), "offset": offset, "limit": limit,
                "next_offset": offset + limit if offset + limit < len(ids) else None}

    def preview_page(self, user, token, *, kind="episodes", include_sensitive=False, offset=0, limit=20):
        if kind not in {"episodes", "facts"} or offset < 0 or not 1 <= limit <= 100:
            raise ForgetError("invalid preview page")
        with self.service.write_lock(user):
            mem = self.service.get(user)
            self.recover_locked(user, mem)
            row = self._record(user, token)
            if row["version"] != self._version(mem, user):
                raise ForgetError("memory changed since preview; preview again", 409)
            return self._items(mem, row["plan"], kind, include_sensitive, offset, limit)

    def confirm(self, user, token, scope, version, *, confirm=False):
        if confirm is not True:
            raise ForgetError("explicit confirm=true is required")
        scope = normalize_scope(scope)
        with self.service.write_lock(user):
            mem = self.service.get(user)
            self.recover_locked(user, mem)
            row = self._record(user, token)
            if (scope != row["scope"] or version != row["version"]
                    or self._version(mem, user) != row["version"]):
                raise ForgetError("scope or memory changed since preview; preview again", 409)
            jobs = getattr(self.service, "jobs", None)
            if jobs is not None and not callable(getattr(jobs, "scrub", None)):
                raise ForgetError("distillation erasure hook is unavailable", 503)
            # A durable no-content intent is written BEFORE deleting. A crash retries the same IDs,
            # never re-evaluates a widening scope. Root's read barrier recovers it before serving data.
            intent = {"id": row["id"], "plan": row["plan"], "status": "pending"}
            self.store.put(user, "forget_intents", intent["id"], intent)
            row["status"] = "consumed"
            self.store.put(user, "forget_previews", row["id"], row)
            self._finish(user, mem, intent)
            return {"ok": True, "counts": row["plan"]["counts"], "scope": scope,
                    "status": "completed", "operation_id": intent["id"]}

    def recover_locked(self, user, mem):
        """Root calls with user write_lock held, before exposing a Memory or running a job."""
        for intent in self.store.list(user, "forget_intents"):
            if intent["status"] == "pending":
                self._finish(user, mem, intent)

    def _finish(self, user, mem, intent):
        try:
            jobs = getattr(self.service, "jobs", None)
            if jobs is not None:
                hook = getattr(jobs, "scrub", None)
                if not callable(hook):
                    raise RuntimeError("missing job cancellation hook")
                hook(user, session_ids=intent["plan"]["session_ids"])
            self._apply(mem, user, intent["plan"])
            self._persist_erasure(user, mem, intent)
            for name, ids in intent["plan"]["review_ids"].items():
                for ident in ids:
                    self.store.delete(user, name, ident)
            for ident in intent["plan"]["connection_ids"]:
                self.store.delete(user, "connection_events", ident)
            for source in intent["plan"]["connection_sources"]:
                remaining = sorted((e for e in self.store.list(user, "connection_events")
                                    if e["source"] == source), key=lambda e: (e["at"], e["id"]))
                if not remaining:
                    self.store.delete(user, "connections", source)
                    continue
                last = remaining[-1]
                self.store.put(user, "connections", source, {
                    "source": source, "attempts": len(remaining),
                    "failures": sum(e["status"] == "failed" for e in remaining),
                    "last_event_at": last["at"], "status": last["status"], "last_error": last["error"],
                    "last_success_at": max((e["at"] for e in remaining if e["status"] != "failed"), default=None),
                })
            # Invalidating all cached evidence references avoids old answers bypassing erasure.
            for ref in self.store.list(user, "evidence_refs"):
                self.store.delete(user, "evidence_refs", ref["id"])
            for preview in self.store.list(user, "forget_previews"):
                self.store.delete(user, "forget_previews", preview["id"])
            self.store.put(user, "forget_intents", intent["id"],
                           {"id": intent["id"], "status": "completed", "completed_at": time.time(),
                            "counts": intent["plan"]["counts"]})
            shutil.rmtree(self._stage_path(user, intent["id"]), ignore_errors=True)
        except Exception as exc:
            raise ForgetError("erasure is pending durable recovery; retry reads after recovery", 503) from exc

    def _stage_path(self, user, operation_id):
        return os.path.join(self.service._path(user), ".forget-stage-" + operation_id)

    def _persist_erasure(self, user, mem, intent):
        # The existing snapshot writer replaces collection files individually. Preserve a COMPLETE
        # sanitized generation before touching the live generation, so a crash between JSONL writes
        # can be recovered before Memory.open sees counts from an older manifest.
        stage = self._stage_path(user, intent["id"])
        original_path = mem._persist_path
        try:
            mem.save(stage)
        finally:
            mem._persist_path = original_path
        self.service._save(user, mem)
        # Keep the stage until the intent is completed (below); a failed status write is recoverable.

    def restore_pending_snapshot_locked(self, user):
        """Call BEFORE Memory.open, under the tenant write lock. Does not read any Memory object."""
        from ..store.persist import COLLECTIONS
        for intent in self.store.list(user, "forget_intents"):
            if intent["status"] != "pending":
                continue
            stage = self._stage_path(user, intent["id"])
            if not os.path.isfile(os.path.join(stage, "manifest.json")):
                continue  # staging never finished, so the original snapshot was not touched
            target = self.service._path(user)
            # Atomic replace per file, manifest last. The stage remains intact until all are installed.
            for name in [*(name + ".jsonl" for name in COLLECTIONS), "manifest.json"]:
                dest = os.path.join(target, name)
                tmp = dest + ".forget.tmp"
                with open(os.path.join(stage, name), "rb") as source, open(tmp, "wb") as output:
                    shutil.copyfileobj(source, output)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(tmp, dest)

    def _apply(self, mem, user, plan):
        facts = set(plan["fact_ids"])
        for ident in plan["episode_ids"]:
            mem.episodes_doc.delete(ident)
            mem.episodes_vec.delete(ident)
            mem.summary_vec.delete(ident)
        for ident in plan["summary_ids"]:
            mem.summary_vec.delete(ident)
            ep = mem.episodes_doc.get(ident)
            if ep is not None:
                ep.summary, ep.summary_embedding = "", None
                mem.episodes_doc.put(ep.id, ep)
                # Real backends may hold a copied payload containing the old summary.
                mem.episodes_vec.upsert(ep.id, ep.embedding or [], ep)
        for ident in facts:
            mem.fact_store.delete(ident)
            mem.cold_store.delete(ident)
            mem.graph.delete_relations_for_fact(ident)
        for fact in mem._all_facts():
            if fact.supersedes in facts:
                fact.supersedes = None
                mem._upsert_fact(fact)
        mem.graph.prune_orphan_entities()
        for ident in plan["working_ids"]:
            mem.working_mem.pop(ident, None)
        for ident in plan["conflict_ids"]:
            mem.conflicts.pop(ident, None)
        mem.working_set.clear()
        mem._persona_cache.clear()
        canonical = mem.resolver.resolve(user)
        mem._identity.pop(canonical, None)
        mem._aliases.pop(canonical, None)
        # Extractor identity caches are separate from persisted identity; rebuild to discard them.
        mem._rewire()
