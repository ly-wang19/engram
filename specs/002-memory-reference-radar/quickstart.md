# Quickstart: Validate the Memory Reference Radar

This guide validates the planning artifacts for `002-memory-reference-radar`. It does not run Engram's
runtime or benchmark harness because this feature is documentation and architecture governance.

## Prerequisites

- Repository checkout.
- Shell with `rg`, `curl`, and `git`.

## 1. Confirm the active feature

```bash
cat .specify/feature.json
```

Expected outcome: the file points to `specs/002-memory-reference-radar`.

## 2. Check required artifacts

```bash
test -f specs/002-memory-reference-radar/spec.md
test -f specs/002-memory-reference-radar/plan.md
test -f specs/002-memory-reference-radar/research.md
test -f specs/002-memory-reference-radar/technical-report.zh-CN.md
test -f specs/002-memory-reference-radar/data-model.md
test -f specs/002-memory-reference-radar/quickstart.md
test -f specs/002-memory-reference-radar/contracts/radar-entry.md
test -f specs/002-memory-reference-radar/contracts/adoption-candidate.md
test -f specs/002-memory-reference-radar/checklists/requirements.md
```

Expected outcome: every command exits successfully.

## 3. Check unresolved placeholders

```bash
rg -n "NEEDS CLARIFICATION|\\[FEATURE\\]|\\[DATE\\]|\\[###|ACTION REQUIRED|REMOVE IF UNUSED" \
  specs/002-memory-reference-radar/spec.md \
  specs/002-memory-reference-radar/plan.md \
  specs/002-memory-reference-radar/research.md \
  specs/002-memory-reference-radar/data-model.md \
  specs/002-memory-reference-radar/contracts
```

Expected outcome: no matches.

## 4. Check key governance terms

```bash
rg -n "external-only|clean-room|accuracy/tokens/latency|raw evidence|supersedes|zero-setup" \
  specs/002-memory-reference-radar
```

Expected outcome: matches appear in the plan, research, data model, or contracts, proving the radar
preserves Engram's constitution gates.

## 5. Check public links in the radar

```bash
perl -ne 'print "$1\n" while /\\((https?:\\/\\/[^)]+)\\)/g' \
  specs/002-memory-reference-radar/research.md | sort -u | \
while read url; do
  code=$(curl -L -s -o /dev/null -w '%{http_code}' "$url")
  printf '%s %s\n' "$code" "$url"
done
```

Expected outcome: current active links return successful HTTP status codes. If a source later moves or
fails, update the source entry with a stale-link note and `last_verified` date.

## 6. Review promotion readiness

Open [contracts/adoption-candidate.md](contracts/adoption-candidate.md) and verify every item in
`research.md`'s Initial Priority Queue can be expressed with:

- Engram-native form.
- Strategic bets served.
- Affected surface.
- Benchmark target.
- Accuracy/tokens/latency hypothesis.
- Clean-room notes.
- Rollback criterion.

Expected outcome: the top candidates can move to `/speckit-tasks`; candidates missing these fields
stay research-only.
