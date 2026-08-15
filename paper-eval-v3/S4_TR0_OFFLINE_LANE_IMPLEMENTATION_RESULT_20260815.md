# S4 TR0 Offline Lane Implementation Result

Date: 2026-08-15

## Outcome

`TR0_SCHEDULING_TRACE_REPLAY` now has an independent, offline-only
implementation and TDD contract. It does not import, call, or inherit the
legacy candidate-level D0 replay path. No model, network, wall clock, Neo4j,
or filesystem operation is performed by the replay module.

This result qualifies only the offline implementation surface. It is not a
TR0 paper result, a real-system calibration, a headline performance result, or
a semantic-correctness result. No real measured-work trace was executed or
sealed in this implementation step.

## Contract

The implementation fail-closes unless the measured-work trace has:

```text
trace_complete = true
checkpoint_complete = true
failure_count = 0
lost_count = 0
duplicate_count = 0
contiguous exact source coverage
unique work IDs and source sequences
per-event phase-demand sum = service demand
```

Replay uses integer virtual time with deterministic ordering and worker tie
breaking. Every policy must conserve the exact source set, arrival times, and
service demand exactly once. The sealed verifier recomputes every schedule and
metric rather than accepting reported values.

The artifact boundary fixes TR0 as:

```text
FIXED_DEMAND_COUNTERFACTUAL_SCHEDULING_ONLY
supporting_control_only = true
headline_performance_source = false
semantic_correctness_oracle = false
real_system_calibration_required = true
paper_claim_authorized = false
legacy_authority_inheritance_allowed = false
live / S5-live / PILOT / formal execution = false
```

It hash-binds the parent protocol, S4 amendment document and artifact, current
stage pointer, trace, and implementation/test sources. The implementation
also records the dynamic effects intentionally omitted by fixed-demand replay,
including vLLM batching and queueing, database contention, concurrency-dependent
service time, state-dependent work generation, changing search demand, and
commit-order feedback.

## TDD Evidence

RED was recorded before the implementation existed:

```text
logs/TDD_RED_S4_TR0_TRACE_REPLAY_20260815.xml
SHA256 a5165e7564e737c913923e5aed0cb5595d25e0cfe3bb54d46263a4c45971f9fd
result 1 collection error: module absent
```

Focused GREEN:

```text
logs/TDD_FOCUSED_GREEN_S4_TR0_TRACE_REPLAY_20260815.xml
SHA256 32fedeeb4521dfa97ed158d32ebf9dce56449359a1a11005b035dd4288ab8907
result 17 passed in 0.03 s
```

Source identities at focused GREEN:

```text
src/paper_eval/s4_tr0_trace_replay.py
SHA256 b8d3edc99e41ff6df7e5748798cf26bc1be16763ffac43c29eb7487db4bdcf64

tests/test_s4_tr0_trace_replay.py
SHA256 53fb4286e97aa22e2d905f16b8aca4825721f6a617e558bd68d7932c19112e11
```

`compileall` and `git diff --check` also completed successfully for the two
implementation files.

## Next Gate

A future TR0 result requires a complete, minimally instrumented trace from an
actual real-system run and exact file/payload bindings. Before TR0 supports any
paper claim, a calibration acceptance rule must be preregistered without seeing
calibration results and then checked against Native and a changed policy at
both low load and near saturation. That future work remains unauthorized by
this implementation result.
