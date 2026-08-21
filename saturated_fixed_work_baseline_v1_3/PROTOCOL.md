# MemBind Saturated Fixed-Work Construction Baseline v1.3

Status: prospective fixed-work baseline with a simplified live execution path.
The v1.2 implementation remains the reusable runner, dataset, namespace,
instrumentation, QA, and reducer dependency.

## 1. Scientific question

The campaign fixes workload `W`, resource envelope `R`, ordered saturated
submission semantics `S`, and measurement `M`.  The only primary independent
variable is execution policy `A`:

* `B0_NATIVE_SERIAL`: await durable completion of each `add_episode` before
  admitting the next source-ordered episode.
* `B1_NAIVE_WHOLE_UPDATE_ASYNC`: create one task per source-ordered episode
  without an application semaphore, worker pool, artificial arrival, or
  scheduler, then drain all tasks with `asyncio.gather`.

The four frozen histories (`07741c45`, `b6019101`, `6071bd76`, `a2f3aa27`),
188 episodes, source order, semantic timestamps, QA inventory, warmup,
counterbalance, first-valid attempt, failed-attempt preservation, namespace
isolation, canonical graph comparison, direct semantic violations, Multi-QA,
and the L1-L5 stage order are inherited unchanged from v1.2.

## 2. Frozen v1.2 development evidence

`saturated_fixed_work_baseline_v1_2/artifacts/sfwb-v1-2-dev-20260821-001` and
its immutable STOP remain development history.  They are not part of the v1.3
main table.  v1.3 never edits that root, its STOP, sealed artifacts, test
summary, or TDD journal; it does not create STOP supersession and never tries
to reconstruct historical PIDs, ordinals, or UUIDs.

Old performance numbers are not reused.  The old protocol design is reused
only as an input to this prospective protocol.

## 3. Simplified live gate

L0 checks only the conditions that determine whether a credible construction
measurement can run:

* both HTTP services complete requests;
* Neo4j performs a read and a write/delete canary;
* the frozen workload loads and the B0/B1 runner and instrumentation compose;
* fixed disjoint warmup completes and all backends are idle.

The v1.3 execution path does not collect or gate on resource-evidence,
physical GPU identity, PID/EngineCore identity, CUDA environment, collector
hashes, or historical resource parity. Those older modules remain only for
reading immutable v1.2 history.


## 4. Test qualification

The v1.3 gate is not the ambiguous historical `tests_all_green` boolean.  It
records and requires:

```text
SFWB-v1.3 tests:                    0 unexpected failures
affected targeted/regression tests: 0 unexpected failures
repository-wide suite:              executed and recorded
clean-HEAD failures:                reproducible by exact test ID/signature
NEW_REGRESSION_COUNT:               0
```

Repository-wide failures are accepted only when the exact test ID and failure
signature are reproduced on clean HEAD and are outside the branch change.
Deleting tests, weakening assertions, or relabeling a new failure as existing
is forbidden.  `evaluate_test_qualification` is the executable contract and
`require_test_qualification` is the fail-closed gate.

## 5. L0/L1 qualification

L0 checks the simplified live prerequisites. L1 then runs the fixed 12-episode
sequence `B0-A -> B0-B -> B1` and records existing makespan, throughput, work
volume, concurrency, ordering, canonical graph, and direct semantic evidence.
A semantic or ordering difference is a result to report; only incomplete
execution, runner failure, missing core instrumentation, or a correctness
accounting defect blocks qualification.

## 7. Execution stages

The authorized order is:

```text
P0 protocol migration audit
P1 targeted TDD
L0 current campaign preflight
L1 12-episode qualification (B0-A, B0-B, B1)
L2 one-history rehearsal (07741c45, B0 and B1)
L3 4 histories x B0/B1 = 8 blocks
L4 32 read-only QA rows
L5 reducer and baseline table
STOP
```

L1 uses one fixed 12-episode prefix and is not skipped for a 49-episode run.
B0-A/B0-B establish serial/instrumentation/nondeterminism floor.  B1 checks
task creation order, complete durable drain, trace completeness, work
accounting, canonical correctness, and semantic violations.  A B1 semantic
violation is a baseline result; dropped work, incomplete trace, runner bug,
namespace contamination, or resource mismatch invalidates the attempt.

L2 is a one-history rehearsal and is excluded from the main table.  L3 keeps
the v1.2 counterbalance and obtains one complete valid run per method/history.
L4 constructs once, seals, and runs four frozen read-only questions per
namespace.  L5 validates protocol, workload, current resource identity,
completeness, work volume, correctness, quality, and performance before
computing speedup.

## 8. Work-volume and correctness

Every B0/B1 pair reports makespan, episodes/s, source tokens/s, LLM calls and
tokens, embedding calls/items/tokens, DB node/edge work, retries, failed calls,
GPU telemetry, canonical graph divergence, and direct semantic violations.
`Speedup_B1 = T_B0 / T_B1` is computed only for protocol-valid same-history
pairs.  A speedup cannot be attributed to execution policy when work volume
differs without an explanation.  B1 is not required to be bit-identical to
B0; serial self-divergence is the reference floor.

## 9. TDD and migration boundaries

The migration tests cover: no historical evidence required; B0/B1 envelope
equality; PID restart tolerance; UUID drift rejection; clean-HEAD
pre-existing failure acceptance; branch regression rejection; v1.2 STOP
immutability; and identity/telemetry separation.  Each changed contract has
an observed RED followed by the minimal GREEN implementation.  Stable v1.2
modules are imported or reused; no baseline scheduler, DAG redesign,
speculation, repair, conflict-aware execution, or parameter tuning is added.

This migration stops at protocol freeze, targeted GREEN, and L0 readiness.  It
does not start L0-L5, create a formal run, or emit experimental results.
