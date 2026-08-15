# S5 Native Method Adapters Offline Result

Date: 2026-08-15

Status: offline scheduling and evidence contract qualified. No Graphiti
runtime, model, network, Neo4j, namespace, live result, or current-stage
authority was created.

## Scope

This increment implements only the injectable scheduling boundary for:

```text
A0       FIFO durable admission with one Native worker
P*, C=2  two complete Native-call workers with measured interval overlap
```

The adapter receives an opaque episode and exactly one injected async
`native_add_episode` callable. Episode bodies are never copied into public
evidence. Historical C4/C5 authority, namespaces, schedules, and results are
not accepted by the public method specification.

This is not yet a production identity. The following remain unqualified:

```text
exact Graphiti add_episode callable binding and dependency closure
S5-specific fsynced event/checkpoint store
P* treatment-failure terminal-classification policy in the live binding
single-use live authority and fresh namespace preflight
M* production core, journal, failure reconciliation, and FX0 parity
```

## TDD Evidence

```text
initial RED
  logs/TDD_RED_S5_NATIVE_METHOD_ADAPTERS_20260815.xml
  1 collection error; implementation module absent

focused GREEN v1
  logs/TDD_FOCUSED_GREEN_S5_NATIVE_METHOD_ADAPTERS_20260815.xml
  15 passed

QA RED
  logs/TDD_QA_RED_S5_NATIVE_METHOD_ADAPTERS_20260815.xml
  17 tests, 2 failures
  exposed permissive extra-field and timestamp verification

focused GREEN v2
  logs/TDD_FOCUSED_GREEN_S5_NATIVE_METHOD_ADAPTERS_V2_20260815.xml
  17 passed

full offline regression
  logs/TDD_FULL_OFFLINE_GREEN_S5_NATIVE_METHOD_ADAPTERS_20260815.xml
  1014 passed, 0 failed, 0 errors, 0 skipped
```

## Identities

```text
source
  a8f0e2d8523c1a67c3307db48ffd4898af6425c2215b85ac854f02c923e04d8f

test
  167130817bdd40092398dae5c1c202b237bb089936c514fc1f6c9e703accbc52

initial RED JUnit
  08178a6ba724f78af3628e39c0706795fb76d30601c035bcb602406c0151e6f1

QA RED JUnit
  25521a53bd5ebc38b86fc35cb7d2a56fd3f10cf657e6f62d429117783a7f195f

focused GREEN v2 JUnit
  be0b396bb7e8dd286e68e6f6a30a23e07281385b1d60561a0cab35abb2432ba4

full offline GREEN JUnit
  c0a193f010b8c69330acb6492e6099d11fa995a06a518b96627b3ee91360412e
```

## Interpretation

The tests establish that the small adapter core can preserve A0 FIFO behavior,
measure real P(C=2) overlap, keep public evidence sanitized, and fail closed on
identity, shape, accounting, worker, timestamp, or durability-hook drift.
They do not establish that a future live entry point invokes the pinned
Graphiti path or that its persistence hook is durable. Those are the next
offline qualification boundaries and must pass before any S5 live authority
is considered.
