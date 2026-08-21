# Changelog

All notable changes to Engram are documented here. The project uses semantic versioning for distributed
artifacts; storage schema compatibility is documented separately when it changes.

## [Unreleased]

### Added

- Native export→import roundtrip (`format="engram"`, auto-sniffed): a `/v1/export` payload now restores
  directly into another instance via `POST /v1/import`, `Memory.import_export()`, the `engram_import`
  MCP tool, or `python -m engram.connectors` — preserving fact ids, bi-temporal stamps, supersession
  chains, and provenance. Idempotent by id; the target re-embeds with its own embedder, which is also
  the supported embedder-migration path.
- `ENGRAM_STORAGE` environment variable selects the vector backend (`memory` default, `lancedb`
  opt-in) for the server/MCP surfaces; unknown values fail closed at startup.
- Optional Bearer authentication for the MCP streamable-HTTP transport (`--http-token` /
  `ENGRAM_MCP_HTTP_TOKEN`). Non-loopback `--http` binds without a token are refused at startup unless
  `ENGRAM_MCP_HTTP_OPEN=1` explicitly delegates access control to an external layer.

### Fixed

- `POST /v1/import` returns 400 with the parser's reason on malformed payloads instead of an
  unhandled 500.
- The import CLI's local mode writes through `MemoryService`, so it uses the same digest-backed
  namespace directories and locks as the HTTP/MCP surfaces (previously a namespace like `a/b` landed
  in a different directory than the one the server reads).
- `/v1/stats` now filters by the canonical linked identity, matching every other read path.

## [0.1.0] - 2026-07-14

### Added

- A bounded self-hosted commercial distribution with Python package, HTTP service, MCP surface,
  TypeScript SDK, management console, Docker Compose deployment, and systemd alternative.
- Separate liveness (`/health`) and readiness (`/ready`) semantics.
- Zero-dependency release gate covering versions, licenses, documentation, deployment assets, and raw
  benchmark evidence links.
- Chinese-first deployment, backup, restore, upgrade, rollback, key rotation, security, and release docs.

### Security

- Replaced character-stripped namespace paths with deterministic digest-backed directories.
- Prevented dot-directory traversal, absolute-path escape, normalization collisions, and cross-tenant
  deletion targets while retaining valid legacy directory/pickle compatibility.
- Added strict static API-key parsing, one-tenant/multiple-key rotation, ambiguous-key rejection,
  constant-time key comparison, request-size limits, field bounds, and browser/API security headers.
- Production container runs as non-root, drops Linux capabilities, uses a read-only root filesystem,
  persists only `/data`, and does not enable open mode by default.

### Changed

- Unified the Python package, service, TypeScript SDK, console, and persistence manifest version at 0.1.0.
- Package development status moved from Alpha to Beta with an explicit single-node support boundary.
- Standard self-hosting examples now use configured API keys; open mode remains development-only.

### Compatibility

- Existing safe namespace directories and trusted legacy pickle snapshots remain readable.
- New namespaces use digest-backed directory names. A rollback to pre-0.1.0 binaries may not discover
  those new directories, so back up data and retain the 0.1.0 binary before rollback.
- Memory extraction, retrieval, ranking, context assembly, and published benchmark numbers are unchanged
  by this release.

### Known Limits

- Single-node only; no multi-region HA, automatic sharding, online distributed migration, billing,
  enterprise SSO/RBAC, compliance certification, or hosted-cloud SLA is claimed.
- TLS, WAF, internet-facing rate limits, and centralized secret management belong at the deployment edge.
