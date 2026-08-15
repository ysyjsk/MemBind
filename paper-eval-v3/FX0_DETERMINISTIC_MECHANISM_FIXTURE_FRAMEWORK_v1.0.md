# FX0 Deterministic Mechanism Fixture Framework v1.0

Status: offline framework qualification only. This file is subordinate to
`S4_VALIDATION_BOUNDARY_AMENDMENT_v2.0.md` and does not authorize model calls,
Neo4j mutation, S5 live execution, PILOT, or formal evaluation.

## Boundary

FX0 invokes an injected production mechanism adapter. The harness does not
implement a second Graphiti or MemBind algorithm. The adapter must expose a
`production_path_identity` and `execute_fixture_case(case, providers)` method;
the identity is currently `PLACEHOLDER_NOT_FROZEN` for M*. A framework PASS
therefore qualifies the harness and its oracle contract only. M* exact-parity
qualification remains `NOT_EXECUTED` until the production identity is frozen
in S5.

The `case` object passed to the adapter is an oracle-free `Fx0ExecutionCase`.
It contains only `case_id`, `source_sequence`, and `source`; expected status,
error code, canonical state, and publication history remain private to the
harness comparator. This prevents a test or production adapter from passing
by reading or echoing its expected answer. The current adapter is still a test
double, so its evidence class is explicitly
`HARNESS_SELF_TEST_WITH_TEST_DOUBLE_ONLY`.

The only replacement providers allowed by the contract are:

* LLM responses;
* embeddings;
* logical time;
* initial graph state; and
* candidate sets.

No raw source, prompt, response, API credential, or private output is written
to the public artifact. Case inputs are represented by SHA256 bindings.

## Required Coverage

Fixture size is determined by transition coverage, not by a fixed episode
count. The current minimum inventory is:

* entity alias/canonical merge;
* compatible duplicate UUID coalescing;
* conflicting duplicate UUID fail-closed;
* relation resolution;
* temporal invalidation/update;
* prepare-to-bind state change;
* source-ordered publication;
* retry/idempotence; and
* lost, duplicate, and partial publication detection.

The final item requires three explicit fail-closed modes: `LOST_PUBLICATION`,
`DUPLICATE_PUBLICATION`, and `PARTIAL_PUBLICATION`. Every case must match both
the expected canonical logical state and the expected publication history in
canonical JSON form. Runtime UUIDs, physical database IDs, and uncontrolled
wall-clock fields are not semantic identities.

## Artifact Interpretation

The sealed artifact binds the parent protocol, this amendment, and the
current-stage pointer by SHA256. It records
`framework_verdict=HARNESS_SELF_TEST_PASS`, exact harness comparison for the
covered test-double cases, `performance_claims_authorized=false`,
`semantic_correctness_claims_authorized=false`,
`m_star_mechanism_correctness_claim_authorized=false`, and
`legacy_authority_inheritance=false`. Model calls, Neo4j reads/writes, all live
S5/PILOT/formal work, and pointer advancement are explicitly false. It must
not be used as headline performance or M* correctness evidence.

## TDD Evidence

The contract tests are in `tests/test_fx0_mechanism_fixture.py`. The initial
RED collection failure, the oracle-isolation RED, and both focused GREEN JUnit
records are persisted under `logs/`. The verifier rejects duplicate case IDs,
invalid source sequences, contradictory PASS/error rows, unregistered
fail-closed errors, and malformed conflicting-duplicate rows. The focused
suite must remain green before any future adapter identity or S5 qualification
work is considered.
