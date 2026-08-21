# MemBind Saturated Fixed-Work Construction Baseline v1.3

Status: prospective protocol, frozen for migration review.  This directory is
not a new formal run and contains no live result.  The v1.2 implementation is
the reusable runner, dataset, namespace, instrumentation, QA, and reducer
dependency; v1.3 changes only the resource and test-qualification contracts.

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

## 3. Current campaign resource envelope

At campaign preflight, capture one `RESOURCE_ENVELOPE_ID` from live evidence.
It must bind, at minimum:

* provider hostname and physical GPU model, UUID, and memory;
* construction Qwen3-32B-FP8, served name, port 8000, max model length
  65536, GPU memory utilization 0.75, structured outputs backend, prefix
  caching, chunked prefill, and FCFS;
* embedding Qwen3-Embedding-0.6B, port 8001, max model length 32768, GPU
  memory utilization 0.15, max batched tokens 32768, and max sequences 128;
* vLLM, Graphiti, and Neo4j versions; checkpoint/model identity; runner commit;
  workload manifest hash; protocol/config hash.

The gate proves `R(B0) == R(B1)` and, for later MemBind comparison,
`R(B0) == R(B1) == R(MemBind)` within this campaign.  It does not require
equality with a historical development host.

Stable identity consists of physical UUID/model, model revision, software
versions, launch configuration, and resource limits.  PID, EngineCore PID,
boot-specific process identity, process tree, and telemetry are ephemeral.
After a vLLM restart, the identity lane is recollected before a new attempt:
unchanged stable identity permits the attempt; UUID/model/config/resource-limit
drift fails closed.

## 4. Identity and telemetry lanes

`ResourceIdentitySnapshot` is collected at campaign preflight and restart
boundaries.  `ProviderTelemetrySample` is collected at 1 Hz and contains only
GPU utilization, memory used, power, clocks, and temperature (plus the pinned
vLLM/runner/Neo4j lightweight metrics).  The 1 Hz sampler must not scan
`/proc`, hash model files, query package versions, or rebuild the envelope.

The provider-side `resource-evidence` collector remains a prospective live
identity source.  It is not a historical-forensics system.  The controller
uses the authorized fixed RPC and validates its canonical response; no
arbitrary remote shell command is constructed.

## 5. Test qualification

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

## 6. L0 current campaign preflight

L0 checks only the new campaign: frozen workload manifest; captured and shared
resource envelope; healthy ports 8000/8001 and Neo4j; exact models/config;
idle services; fixed disjoint warmup; available telemetry; and test
qualification with `NEW_REGRESSION_COUNT == 0`.  Historical parity is not a
gate.  A passing readiness result authorizes a future new run but does not
create one here.

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
