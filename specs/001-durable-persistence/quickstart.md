# Quickstart — Validating Durable Persistence

Runnable scenarios that prove the feature end-to-end. **No performance numbers are asserted here** — any
latency/scale figure is a separate, harness-measured task with a committed log (Constitution I & V).

## Prerequisites
- US1 / US3: nothing beyond the repo (zero-setup — Constitution II).
- US2: `pip install -e '.[lancedb]'`.

## Scenario A — Survive a restart with zero loss (US1 · SC-001 · INV-1..3)
```bash
python3 - <<'PY'
from engram import Memory
m = Memory.open("data/qs/alice")                 # creates/opens a JSONL+manifest store directory
m.add("My flight is AA100 to Boston on Friday.")
m.add("Actually the flight changed to AA200.")   # creates a supersedes chain
m.consolidate()
m.save()
PY
python3 - <<'PY'                                  # reopen in a fresh process
from engram import Memory
m = Memory.open("data/qs/alice")
print(m.search("which flight?").answer())         # AA200; AA100 still stored but invalidated
PY
```
**Expected**: all episodes/facts reload; the superseded AA100 fact persists and stays invalid; provenance +
bi-temporal stamps intact.

## Scenario B — Zero-setup default unchanged (Constitution II · SC-004)
```bash
pip uninstall -y lancedb 2>/dev/null; pytest -q   # full offline suite — passes with NO extras
python3 examples/quickstart.py                    # hashing embedder + in-memory stores
```
**Expected**: green. `tests/test_zero_setup_default.py` asserts the default import graph never imports
`lancedb`.

## Scenario C — Safe load + version gate (SC-002 · FR-003 · FR-004)
```bash
pytest -q tests/test_persist_safety.py
```
**Expected**: a crafted store file cannot trigger code execution on open; a manifest with a future
`schema_version` raises `IncompatibleStoreError`.

## Scenario D — Crash recovery (SC-003 · FR-005)
```bash
pytest -q tests/test_persist_crash.py
```
**Expected**: a JSONL file truncated mid-line loads the committed prefix and drops only the torn record.

## Scenario E — LanceDB backend persists + parity (US2 · SC-005 · FR-009)
```bash
pip install -e '.[lancedb]'
pytest -q tests/test_lancedb_store.py
```
**Expected**: vectors persist across restart; reloaded top-k matches the in-memory store within tolerance.

Minimal direct use:
```bash
python3 - <<'PY'
from engram import Memory
from engram.config import Config

cfg = Config(storage="lancedb", data_path="data/qs/alice-vectors")
m = Memory.open("data/qs/alice-lancedb", config=cfg)
m.add_fact("user", "works_at", "Moonshot AI", user_id="alice")
m.save()

reopened = Memory.open("data/qs/alice-lancedb", config=cfg)
print(reopened.search("Where do I work?", user_id="alice").answer())
PY
```
**Expected**: prints `Moonshot AI`; the JSONL manifest stores the portable snapshot and LanceDB stores the
vector tables under `data_path`.

## Scenario F — Migrate off pickle (US3 · FR-008)
```bash
python3 -m engram.store.migrate --from data/legacy/alice.pkl --to data/qs/alice --dry-run  # prints counts, writes nothing
python3 -m engram.store.migrate --from data/legacy/alice.pkl --to data/qs/alice            # writes JSONL
# installed package equivalent:
#   engram-migrate-pickle --from data/legacy/alice.pkl --to data/qs/alice
```
**Expected**: the dry-run reports per-entity counts and writes nothing; the real run yields entity parity
with the pickle. Only run this command on trusted legacy snapshots; normal `Memory.open()` does not load
pickle.
