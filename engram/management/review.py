"""Persistent, namespace-scoped review state; memory contents remain in their owning stores."""
from __future__ import annotations

import hashlib
import json
import time
from collections import Counter

from ..consolidate.outcomes import OUTCOME_PREDICATES, split_outcome_text
from ..localize import display_of
from ..util import fmt_datetime


class ReviewError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _public_history(record, include_sensitive):
    events = record.get("history", [])
    if include_sensitive:
        return events
    return [{"at": e["at"], "status": e["status"], "reason": e["reason"], "note": ""} for e in events]


def _page(rows, limit, offset):
    if not 1 <= limit <= 100 or offset < 0:
        raise ReviewError("limit must be 1..100 and offset must be non-negative")
    end = offset + limit
    return {"items": rows[offset:end], "page": {"total": len(rows), "limit": limit,
            "offset": offset, "has_more": end < len(rows),
            "next_offset": end if end < len(rows) else None}}


def _history(record, status, reason, note="", **extra):
    event = {"at": time.time(), "status": status, "reason": reason, "note": note, **extra}
    return {**record, "status": status, "note": note, "updated_at": event["at"],
            "history": [*record.get("history", []), event]}


class ReviewQueue:
    def __init__(self, service):
        self.service = service
        self.store = service.management_store

    def _facts(self, user):
        mem = self.service.get(user)
        canonical = mem.resolver.resolve(user)
        facts = {f.id: f for f in mem.fact_store.values() + mem.cold_store.values()
                 if mem.resolver.resolve(f.user_id) == canonical}
        return mem, facts

    def _fingerprint(self, mem, facts):
        # Access counters/salience are deliberately excluded: recall does not change source evidence.
        return _hash([{
            "id": f.id, "subject": f.subject, "predicate": f.predicate, "object": f.object,
            "text": f.text, "valid_at": f.valid_at, "invalid_at": f.invalid_at,
            "created_at": f.created_at, "expired_at": f.expired_at, "source": f.source,
            "sensitive": f.sensitive, "supersedes": f.supersedes,
            "provenance": [{"id": eid, "content": ep.content if ep else None,
                            "summary": ep.summary if ep else None,
                            "event_time": ep.event_time if ep else None}
                           for eid in sorted(f.provenance)
                           for ep in [mem.episodes_doc.get(eid)]]}
            for f in sorted(facts, key=lambda f: f.id)])

    @staticmethod
    def _view(f):
        return {"id": f.id, "text": f.text, "display": display_of(f), "subject": f.subject,
                "predicate": f.predicate, "object": f.object, "valid_at": fmt_datetime(f.valid_at),
                "invalid_at": fmt_datetime(f.invalid_at) if f.invalid_at else None,
                "status": "live" if f.is_live() else "superseded", "source": f.source,
                "supersedes": f.supersedes, "category": f.category, "sensitive": f.sensitive,
                "salience": round(f.salience, 2), "provenance": f.provenance,
                "why": split_outcome_text(f.text)[1]}

    def outcomes(self, user, *, kind="all", status="live", q="", include_sensitive=False,
                 limit=30, offset=0):
        if kind not in {*OUTCOME_PREDICATES, "all", "resolution"} or status not in {"live", "superseded", "all"}:
            raise ReviewError("Invalid outcome kind or status")
        with self.service.write_lock(user):
            _, facts = self._facts(user)
            rows = [f for f in facts.values() if f.predicate.lower() in OUTCOME_PREDICATES
                    and (include_sensitive or not f.sensitive)
                    and (status == "all" or f.is_live() == (status == "live"))
                    and (kind == "all" or f.predicate.lower() == kind
                         or kind == "resolution" and f.predicate.lower() != "open_question")]
            needle = q.strip().casefold()
            rows = [f for f in rows if needle in " ".join((f.text, f.subject, f.object, display_of(f))).casefold()]
            rows.sort(key=lambda f: (-f.valid_at, f.id))
            return _page([self._view(f) for f in rows], limit, offset)

    def _question(self, user, mem, facts, fact, include_sensitive):
        source = self._fingerprint(mem, [fact])
        record = self.store.get(user, "review_questions", fact.id) or {
            "id": fact.id, "status": "open", "source_version": source,
            "resolution_id": None, "history": [], "note": "", "updated_at": 0,
        }
        resolution = facts.get(record.get("resolution_id"))
        changed = record["source_version"] != source
        invalid_resolution = record["status"] == "resolved" and (
            resolution is None or not resolution.is_live()
            or resolution.predicate.lower() not in set(OUTCOME_PREDICATES) - {"open_question"}
            or record.get("resolution_version") != self._fingerprint(mem, [resolution]))
        if changed or invalid_resolution:
            record = _history(record, "open", "source_changed" if changed else "resolution_changed")
            record.update(source_version=source, resolution_id=None, resolution_version=None)
            self.store.put(user, "review_questions", fact.id, record)
            resolution = None
        hidden = resolution is not None and resolution.sensitive and not include_sensitive
        return {**self._view(fact), "review_status": record["status"],
                "version": _hash(record), "source_changed": changed or invalid_resolution,
                "resolution_id": None if hidden else record.get("resolution_id"),
                "resolution": self._view(resolution) if resolution is not None and not hidden else None,
                "resolution_hidden": hidden, "note": record.get("note", "") if include_sensitive else "",
                "history": _public_history(record, include_sensitive),
                "updated_at": record["updated_at"]}

    def questions(self, user, *, status="open", q="", include_sensitive=False, limit=20, offset=0):
        if status not in {"open", "resolved", "all"}:
            raise ReviewError("Invalid question status")
        with self.service.write_lock(user):
            mem, facts = self._facts(user)
            rows = [self._question(user, mem, facts, f, include_sensitive) for f in
                    sorted(facts.values(), key=lambda f: (-f.valid_at, f.id))
                    if f.is_live() and f.predicate.lower() == "open_question"
                    and (include_sensitive or not f.sensitive)]
            needle = q.strip().casefold()
            rows = [r for r in rows if needle in " ".join((r["text"], r["subject"], r["object"], r["display"])).casefold()]
            counts = dict(Counter(r["review_status"] for r in rows))
            return {**_page([r for r in rows if status == "all" or r["review_status"] == status], limit, offset),
                    "by_status": counts}

    def update_question(self, user, fact_id, *, status, expected_version, resolution_id=None,
                        note="", include_sensitive=False):
        if status not in {"open", "resolved"}:
            raise ReviewError("Invalid question status")
        if len(note) > 2000:
            raise ReviewError("Note is too long")
        with self.service.write_lock(user):
            mem, facts = self._facts(user)
            fact = facts.get(fact_id)
            if fact is None or not fact.is_live() or fact.predicate.lower() != "open_question" or (fact.sensitive and not include_sensitive):
                raise ReviewError("Question not found", 404)
            current = self._question(user, mem, facts, fact, include_sensitive)
            if current["version"] != expected_version:
                raise ReviewError("Question or its resolution changed; refresh before updating", 409)
            resolution = facts.get(resolution_id)
            if status == "resolved" and (resolution is None or not resolution.is_live()
                    or resolution.predicate.lower() not in set(OUTCOME_PREDICATES) - {"open_question"}
                    or resolution.sensitive and not include_sensitive):
                raise ReviewError("Choose a current resolution outcome in this namespace", 422)
            source = self._fingerprint(mem, [fact])
            old = self.store.get(user, "review_questions", fact_id) or {"id": fact_id, "history": []}
            record = _history(old, status, "user_resolved" if status == "resolved" else "user_reopened",
                              note.strip(), resolution_id=resolution_id if status == "resolved" else None)
            record.update(source_version=source, resolution_id=resolution_id if status == "resolved" else None,
                          resolution_version=self._fingerprint(mem, [resolution]) if status == "resolved" else None)
            self.store.put(user, "review_questions", fact_id, record)
            return self._question(user, mem, facts, fact, include_sensitive)

    def _scan(self, user):
        mem, facts = self._facts(user)
        audit = self.service.audit(user, limit=2147483647)
        rows = []
        for finding in audit["findings"]:
            kind = finding["kind"]
            if finding.get("fact_id"):
                target = ["fact", finding["fact_id"]]
                sources = [facts[finding["fact_id"]]] if finding["fact_id"] in facts else []
            elif kind == "slot_overflow":
                target = ["slot", finding["subject"], finding["predicate"]]
                sources = [f for f in facts.values() if f.is_live() and f.source != "user"
                           and f.subject == finding["subject"] and f.predicate == finding["predicate"]]
            elif finding.get("entity"):
                target = ["entity", finding["entity"]]
                sources = [f for f in facts.values() if finding["entity"] in (f.subject, f.object)]
            else:
                target = ["store"]
                sources = list(facts.values())
            ident = _hash([kind, target])
            fingerprint = _hash([finding, self._fingerprint(mem, sources)])
            rows.append({**finding, "id": ident, "source_version": fingerprint,
                         "fact_ids": sorted(f.id for f in sources),
                         "sensitive": any(f.sensitive for f in sources), "present": True})
        return audit, rows

    def _health(self, user):
        audit, findings = self._scan(user)
        previous = {r["id"]: r for r in self.store.list(user, "review_health")}
        current = set()
        rows = []
        for finding in findings:
            ident = finding["id"]
            current.add(ident)
            old = previous.get(ident)
            changed = old is not None and (old["source_version"] != finding["source_version"] or not old.get("present", True))
            record = old or {"id": ident, "status": "open", "history": [], "note": "", "updated_at": 0}
            if changed:
                record = _history(record, "open", "source_changed")
            record = {**record, **{k: finding[k] for k in ("id", "kind", "source_version", "fact_ids", "sensitive", "present")}}
            if record != old:
                self.store.put(user, "review_health", ident, record)
            rows.append({**finding, **record, "version": _hash(record), "source_changed": changed})
        for ident, old in previous.items():
            if ident in current:
                continue
            record = old
            if old.get("present", True):
                record = _history(old, "resolved", "no_longer_flagged")
                record["present"] = False
                self.store.put(user, "review_health", ident, record)
            rows.append({**record, "version": _hash(record), "source_changed": False,
                         "why": "This source is no longer flagged by the current rules.", "action": "view history"})
        rows.sort(key=lambda r: (not r["present"], r["kind"], r["id"]))
        return audit, rows

    def health(self, user, *, kind="all", status="open", q="", include_sensitive=False,
               limit=30, offset=0):
        if status not in {"open", "ignored", "resolved", "all"}:
            raise ReviewError("Invalid health status")
        with self.service.write_lock(user):
            audit, rows = self._health(user)
            rows = [r for r in rows if include_sensitive or not r["sensitive"]]
            if not include_sensitive:
                rows = [{**r, "note": "", "history": _public_history(r, False)} for r in rows]
            needle = q.strip().casefold()
            rows = [r for r in rows if needle in " ".join(str(r.get(k, "")) for k in ("text", "entity", "kind", "why")).casefold()]
            by_kind = dict(Counter(r["kind"] for r in rows))
            by_status = dict(Counter(r["status"] for r in rows))
            filtered = [r for r in rows if (kind == "all" or r["kind"] == kind)
                        and (status == "all" or r["status"] == status)]
            return {**_page(filtered, limit, offset), "checked": audit["checked"],
                    "total_findings": sum(r["present"] for r in rows), "by_kind": by_kind, "by_status": by_status}

    def update_health(self, user, finding_id, *, status, expected_version, note="", include_sensitive=False):
        if status not in {"open", "ignored", "resolved"} or len(note) > 2000:
            raise ReviewError("Invalid health status or note")
        with self.service.write_lock(user):
            _, rows = self._health(user)
            row = next((r for r in rows if r["id"] == finding_id and (include_sensitive or not r["sensitive"])), None)
            if row is None:
                raise ReviewError("Finding not found", 404)
            if row["version"] != expected_version or not row["present"]:
                raise ReviewError("Finding changed or is no longer flagged; refresh before updating", 409)
            old = self.store.get(user, "review_health", finding_id)
            record = _history(old, status, "user_" + status, note.strip())
            self.store.put(user, "review_health", finding_id, record)
            return {**row, **record, "version": _hash(record),
                    "note": record["note"] if include_sensitive else "",
                    "history": _public_history(record, include_sensitive)}
