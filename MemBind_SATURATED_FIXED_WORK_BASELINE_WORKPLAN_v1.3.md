# MemBind Saturated Fixed-Work Construction Baseline Workplan v1.3

**Status:** prospective formal protocol frozen for minimal migration review.
No formal run, live result, STOP seal, or experiment is created by this file.
The executable companion is
[`saturated_fixed_work_baseline_v1_3/PROTOCOL.md`](/data/predator/ly/MemBind/saturated_fixed_work_baseline_v1_3/PROTOCOL.md).

## Decision

The v1.2 development run is permanently frozen as development evidence.  Its
performance numbers do not enter the v1.3 main table, and its provider UUID,
PID, ordinal, or historical resource envelope is not a v1.3 gate.  v1.3
reuses the v1.2 frozen workload and stable execution stack, then runs B0 and B1
again in one new campaign.

## Unchanged protocol surface

The following remain exactly as v1.2: four histories
(`07741c45`, `b6019101`, `6071bd76`, `a2f3aa27`), 188 fixed episodes and source
order, semantic timestamps, QA inventory, ordered saturated submission, no
synthetic arrival/`rho`/think time, fixed disjoint warmup, backend-idle and
build-lifecycle timing, work accounting, canonical graph correctness, direct
semantic violations, Multi-QA, counterbalance, first-valid attempt,
failed-attempt preservation, namespace isolation, L1 qualification, L2
rehearsal, eight L3 blocks, 32 L4 QA rows, and the L5 reducer.

## Two protocol corrections

1. **Historical parity removed.**  L0 freezes a new current-campaign
   `RESOURCE_ENVELOPE_ID`; it requires the same stable physical/software/config
   identity for B0 and B1 (and later MemBind), but makes no claim about the old
   development host.  GPU UUID/model/checkpoint/version/config/limits are
   stable identity.  PID, EngineCore PID, boot ID, process tree, and telemetry
   are ephemeral.  A restart requires a fresh identity snapshot; unchanged
   stable identity permits a new attempt, drift fails closed.
2. **Test qualification clarified.**  SFWB-owned and affected targeted tests
   must have zero unexpected failures.  The repository-wide suite is run and
   recorded.  A repository failure is pre-existing only when clean HEAD has the
   exact same test ID and failure signature and it is outside the branch
   change.  The gate is `NEW_REGRESSION_COUNT == 0`; `tests_all_green` is not a
   v1.3 semantic.

## Execution and stop boundary

The future campaign order is `P0 -> P1 -> L0 -> L1 -> L2 -> L3 -> L4 -> L5`.
L1 is the fixed 12-episode `B0-A, B0-B, B1` qualification, followed by the
one-history rehearsal and the eight preregistered B0/B1 blocks.  This migration
only delivers the frozen v1.3 protocol, the prospective gate adapter, targeted
RED-to-GREEN tests, and a pure L0 readiness contract.  It does not execute
L0-L5 or create a new formal run.

## Implementation mapping

* Stable v1.2 runner, dataset, namespace, instrumentation, telemetry, QA, and
  reducer modules remain the dependency closure.
* New current-envelope and regression gates are implemented in
  `saturated_fixed_work_baseline_v1_2/.../v1_3.py` and re-exported by the v1.3
  adapter package.
* Identity discovery is a low-frequency lane; the 1 Hz sampler receives only
  lightweight provider telemetry.
* Migration tests are in
  `saturated_fixed_work_baseline_v1_2/tests/unit/test_p44_v1_3_migration.py`.

The old v1.2 STOP SHA-256 remains bound and unchanged:
`2cd5f9043136865df71085cd92840fa86512982c5fc77be01026fab244af5426`.
