# MemBind Saturated Fixed-Work Construction Baseline Workplan v1.3

**Status:** prospective fixed-work baseline with a simplified live execution
path. The old v1.2 development root remains historical and immutable.
The executable companion is
[`saturated_fixed_work_baseline_v1_3/PROTOCOL.md`](/data/predator/ly/MemBind/saturated_fixed_work_baseline_v1_3/PROTOCOL.md).

## Decision

The v1.2 development run is permanently frozen as development evidence. Its
performance numbers do not enter the v1.3 main table. v1.3 reuses the frozen
workload and stable execution stack, then runs B0 and B1 again in a new
campaign. Physical resource consistency is an operator-controlled experimental
condition, not a protocol gate.

## Unchanged protocol surface

The following remain exactly as v1.2: four histories
(`07741c45`, `b6019101`, `6071bd76`, `a2f3aa27`), 188 fixed episodes and source
order, semantic timestamps, QA inventory, ordered saturated submission, no
synthetic arrival/`rho`/think time, fixed disjoint warmup, backend-idle and
build-lifecycle timing, work accounting, canonical graph correctness, direct
semantic violations, Multi-QA, counterbalance, first-valid attempt,
failed-attempt preservation, namespace isolation, L1 qualification, L2
rehearsal, eight L3 blocks, 32 L4 QA rows, and the L5 reducer.

## Simplified execution contract

L0 checks only conditions that determine whether a credible construction
measurement can run: both HTTP services complete requests, Neo4j can perform a
read and a write/delete canary, the frozen workload loads, the B0/B1 runner and
instrumentation compose, fixed disjoint warmup completes, and all backends are
idle before measurement.

The v1.3 execution path does not collect or gate on resource-evidence,
physical GPU identity, PID/EngineCore identity, CUDA environment, boot or
machine identity, collector deployment hashes, or historical resource parity.
Those older modules remain only for reading immutable v1.2 history.

Qualification runs the fixed 12-episode sequence `B0-A -> B0-B -> B1` and
records existing makespan, throughput, work volume, concurrency, ordering,
canonical graph, and direct semantic evidence. A semantic or ordering
difference is a result to report. Only incomplete execution, runner failure,
missing core instrumentation, or a correctness accounting defect blocks the
qualification.

## Execution and stop boundary

The live qualification boundary is `L0 -> L1`; the simplified qualification
command stops after the three qualification blocks. L2-L5 remain separately
authorized stages and are not started by this command.

## Implementation mapping

* Stable v1.2 runner, dataset, namespace, instrumentation, telemetry, QA, and
  reducer modules remain the dependency closure.
* The simplified live adapter is
  `saturated_fixed_work_baseline_v1_3/.../simple_campaign.py`.
* Targeted adapter tests are in
  `saturated_fixed_work_baseline_v1_3/tests/test_simple_campaign.py`.
* Existing v1.2 instrumentation and reducer primitives are reused without
  adding a scheduler or a new measurement family.

The old v1.2 STOP SHA-256 remains bound and unchanged:
`2cd5f9043136865df71085cd92840fa86512982c5fc77be01026fab244af5426`.
