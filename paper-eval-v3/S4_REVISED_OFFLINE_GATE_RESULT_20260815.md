# Revised S4 Offline Gate Result

Date: 2026-08-15

## Outcome

The proposal to retire full cross-run candidate-level Graphiti replay as the
paper qualification boundary is methodologically sound, with two mandatory
limits retained:

1. TR0 is a fixed-demand scheduling counterfactual and cannot replace real
   Graphiti performance measurements.
2. FX0 proves only covered production-path transitions; a test-double harness
   cannot establish M* correctness before the production identity is frozen.

The unified result is:

```text
status          OFFLINE_FRAMEWORKS_QUALIFIED_ONLY
current stage   S3_CONFIGURATION_FROZEN
next action     S5_PRODUCTION_METHOD_QUALIFICATION_OFFLINE_DESIGN
S5 live         not authorized
model / Neo4j   not authorized
```

Sealed artifact:

`artifacts/paper_eval/native/S4_REVISED_OFFLINE_GATE.json`

```text
file SHA256     5527752c79eaf6fb6b7932bb271f44b534f8d1b6a13762c9c8dce3ba14034e26
payload SHA256  1d652c83951989d36b7988400ed155b1b11b0a76853444cc6743e3a65c9f2531
```

## Preserved Historical Evidence

Retry-008 remains unchanged. Its real Native U0 capture completed 49/49
episodes. The historical D0 replay failed closed at source sequence 7 with
`SIDECAR_CALL_CORRELATION_MISSING`, remains incomplete/non-mergeable, and is
not resumed, cleaned, rewritten, or converted to PASS. No retry-009 is
authorized.

The U0 run is an operational canary, not headline timing evidence, because
candidate-sidecar capture added work.

## Lane Status

### TR0

The deterministic integer-time scheduler implementation and its conservation
checks are qualified offline. No complete measured trace has been sealed, no
real TR0 replay result exists, and the required real-system calibration across
Native and a changed policy at low and near-saturation load is not satisfied.
TR0 therefore has no performance or correctness claim authority.

Implementation:

`src/paper_eval/s4_tr0_trace_replay.py`

Tests:

`tests/test_s4_tr0_trace_replay.py`

### FX0

The harness is qualified only with a test double. During gate review, a real
oracle-isolation defect was found: the original runner passed a fixture object
containing expected results to the adapter. TDD changed the adapter input to
an oracle-free `Fx0ExecutionCase` containing only case identity, source
sequence, and source input. Expected status/state/publication history now stay
inside the comparator.

The artifact verifier also rejects duplicate case IDs, negative or non-integer
source sequences, PASS rows with errors, fail-closed rows with unregistered
errors, and malformed conflicting-duplicate cases. M* production identity and
exact parity remain `NOT_EXECUTED`.

Implementation:

`src/paper_eval/fx0_mechanism_fixture.py`

Tests and framework note:

`tests/test_fx0_mechanism_fixture.py`

`FX0_DETERMINISTIC_MECHANISM_FIXTURE_FRAMEWORK_v1.0.md`

### Real Workload Correctness

The offline contract requires all U0/A0/P*/M* methods to execute real Graphiti
and freezes direct invariant, semantic matching, retrieval, QA, pairing, CI,
and pre-result margin requirements. The semantic matching oracle and numeric
quality margins are not yet frozen, and no workload result has been generated.

Implementation and tests:

`src/paper_eval/real_workload_correctness_contract.py`

`tests/test_real_workload_correctness_contract.py`

## TDD Evidence

Expected RED evidence:

```text
TR0             1 collection error before implementation
FX0             1 collection error before oracle-isolation implementation
Real contract   1 collection error before implementation
Unified gate    1 collection error before implementation
```

Focused GREEN evidence:

```text
TR0             17 passed
FX0             17 passed
Real contract   15 passed
Unified gate    12 passed
```

Integration and full regression:

```text
revised S4 integration   70 passed
paper-eval-v3 full       959 passed, 0 failed, 0 errors, 0 skipped
```

Persistent logs:

```text
logs/TDD_RED_S4_TR0_TRACE_REPLAY_20260815.xml
logs/TDD_INTERMEDIATE_RED_FX0_ORACLE_ISOLATION_20260815.xml
logs/TDD_RED_REAL_WORKLOAD_CORRECTNESS_20260815.xml
logs/TDD_RED_S4_REVISED_OFFLINE_GATE_20260815.xml
logs/TDD_FOCUSED_GREEN_S4_TR0_TRACE_REPLAY_20260815.xml
logs/TDD_FOCUSED_GREEN_FX0_MECHANISM_FIXTURE_V2_20260815.xml
logs/TDD_FOCUSED_GREEN_REAL_WORKLOAD_CORRECTNESS_20260815.xml
logs/TDD_FOCUSED_GREEN_S4_REVISED_OFFLINE_GATE_20260815.xml
logs/TDD_INTEGRATION_GREEN_REVISED_S4_ALL_LANES_20260815.xml
logs/TDD_FULL_GREEN_S4_REVISED_OFFLINE_GATE_20260815.xml
```

## Interpretation

This result closes the validation-boundary redesign, not S4 live qualification.
It prevents the old internal D0 trajectory from blocking the paper while also
preventing trace simulation or a fixture test double from being presented as
real evidence. The next bounded task is offline S5 method qualification:
freeze the actual A0/P/M* production identities, bind the real M* adapter, and
only then run FX0 exact parity and construct a separate one-history live smoke
authority.
