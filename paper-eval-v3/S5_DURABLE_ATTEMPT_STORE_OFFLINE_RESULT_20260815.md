# S5 Durable Attempt Store Offline Result

Date: 2026-08-15

Status: isolated durable-attempt contract qualified offline. The store does
not construct or inspect Graphiti, call a model, touch Neo4j, create a live
namespace, or grant resume authority.

## Guarantees

The store reuses the repository's tested durable primitives and binds them to
new S5 schemas:

```text
manifest first
append-only JSONL event records with event hash and fsync
contiguous event sequence and source/hash identity checks
atomic result and checkpoint replacement
result-to-event binding
tamper/private-field fail-closed inspection
incomplete attempts permanently non-mergeable and non-resumable
```

An existing attempt directory is never reopened by `create`. A failure after a
DB boundary is therefore retained as a terminal attempt; this store does not
claim mid-history recovery or idempotent DB replay.

## TDD Evidence

```text
initial RED
  logs/TDD_RED_S5_DURABLE_ATTEMPT_STORE_20260815.xml
  missing implementation module; collection error

focused GREEN
  logs/TDD_FOCUSED_GREEN_S5_DURABLE_ATTEMPT_STORE_20260815.xml
  7 passed

full offline GREEN
  logs/TDD_FULL_OFFLINE_GREEN_S5_DURABLE_ATTEMPT_STORE_20260815.xml
  1028 passed, 0 failures, 0 errors, 0 skipped
```

The focused suite covers duplicate attempts, manifest-first setup, sequence
gaps, source identity, event hash tampering, rehashed private data, result
binding, atomic checkpoint state, and terminal non-resume semantics.

## File identities

```text
source
  f999fde419547233b8d30ecfabc02944b6db3da829e657264b8fa26ee7417b19

test
  c182a73a27bd8ab2d90d2982fee0d4a7cf1074da30b7e7890841953f9aaad4af

RED JUnit
  64abac6afc21502cd870febd9970792f72ff2e9db0caed4d3161cabd3a19fa12

focused GREEN JUnit
  0ca2eb7dddb87ad8246ae74134bc340a3678aef29c5efb96f78cb47c777be102

full offline GREEN JUnit
  0da399d52d3eab4db2fe8c2a9d6985090478f6c5d4625fd951efc901b2b9a9eb
```

## Remaining production work

This store is a building block, not a live authority. Before S5 M* smoke, a
method-specific runner must bind the store to the pinned Graphiti M* path,
write intent/commit/publication boundaries through this store, and verify a
fresh namespace preflight. Any incomplete or disconnected attempt remains
non-mergeable and is not resumed in place.
