# S5 M* Pipeline Offline Result

Date: 2026-08-15

Status: shared prepare/bind/publication mechanism qualified offline only.
No Graphiti object, model request, network access, Neo4j access, namespace, or
live authority was used.

## Contract

The pipeline is deliberately provider-agnostic:

```text
two concurrent semantic_prepare callbacks
        -> one source-ordered latest_state_bind callback
        -> durable publication events
```

It verifies actual prepare overlap, bind concurrency of one, source-order
publication, latest published prefix, source-to-logical-time stability, exact
source accounting, sanitized evidence, and terminal failure behavior. The
opaque callback values never enter the evidence artifact.

The core does not implement or replace Graphiti extraction, entity
resolution, edge invalidation, temporal maintenance, or Neo4j commit logic.
Those operations must be supplied by the same pinned production path for the
future M* live adapter and by controlled providers for FX0.

## TDD Evidence

```text
initial RED
  logs/TDD_RED_S5_MSTAR_PIPELINE_20260815.xml
  missing implementation module; collection error

focused GREEN after async-wait repair
  logs/TDD_FOCUSED_GREEN_S5_MSTAR_PIPELINE_20260815.xml
  7 passed

durability boundary GREEN
  logs/TDD_FOCUSED_GREEN_S5_MSTAR_PIPELINE_DURABILITY_20260815.xml
  1 passed

full offline GREEN
  logs/TDD_FULL_OFFLINE_GREEN_S5_MSTAR_PIPELINE_20260815.xml
  1021 passed, 0 failures, 0 errors, 0 skipped
```

The first post-implementation focused run exposed the invalid use of
`asyncio.create_task(Future)` and was repaired before the final focused run.
The later durability test exposed a worker-future hang risk; the core now
poisons and awaits workers and raises a stable durability error when it cannot
trust terminal evidence.

## File identities

```text
source
  d3453f7c550afa46b1fd877f6863353abaf7689d4178e5f82dac4ebd5df670ec

test
  13730e1340cfe8423e8ee1c0c86e99f23f6b3003a1e62b5ee7bf4d1082486fde

initial RED JUnit
  3919f82164378c43efe5b9db3279cf2bd46a2d5edf9835ea44873f2be37e382f

durability focused JUnit
  89de734ec340b495fcd7abfe011a46a1f6f59942e408573df1cc8d27864274c3

focused GREEN JUnit
  18b586dbf939f6b26637a9c3bb86b064ca80484cf89f70c6584fef9e9ec96637

full offline GREEN JUnit
  4e829d10d91e237466ec57634d0b110f3d0d8660f17a652ed644957ae948b4ef
```

## Remaining blockers

```text
exact Graphiti production callable and private-API pin
durable S5 event/checkpoint store and root failure artifact
FX0 production adapter using this same core
M* production identity and single-use authority
```

No blocker is resolved by this offline result alone.
