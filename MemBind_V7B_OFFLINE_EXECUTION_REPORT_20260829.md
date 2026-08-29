# MemBind V7-B Offline Execution Report

Date: 2026-08-29  
Scope: provider-free TDD and observer-only characterization under `workplan_v7.md`  
Runtime identity: `local-qwen3-14b-awq-v1` (used only for environment checks)

## Completed

- Added the independent `membind_v7.v7b` reference implementation. It contains a deterministic source-local Stable Semantic IR, explicit stateful view definitions, d=1 dirty propagation, exact reconvergence, conservative fresh fallback, ordered publication validation, and an auditable offline materializer.
- Added `tests/test_membind_v7b_offline.py`. All five tests pass.
- Added `scripts/run_v7b_offline_campaign.py` and executed R2 (one two-source causal pair) plus R3-A/R3-B (six pairs each).
- Added frozen contracts: `v7/V7B_FRESH_ALGORITHM_FROZEN.json`, `v7/V7B_BASELINE_CONTRACT.json`, and `v7/V7B_OFFLINE_GATE_RESULT.json`.
- Fixed a service-startup engineering defect: the validation harness `statistics.py` was shadowing the Python standard-library module inside vLLM. The LLM and embedding launch boundaries now clear `PYTHONPATH` before starting vLLM.

## Offline evidence

The sealed campaign is at:

`/data/predator/ly/Mem/experiments/local-qwen3-14b-awq-v1/v7b-offline-campaign-20260829-r2`

| Metric | Value |
|---|---:|
| pairs | 13 |
| canonical differential (`V7-Inc == V7-Fresh`) | true |
| provider/treatment calls | 0 / 0 |
| mean Stable IR fraction | 0.1613 |
| mean affected repair work fraction | 0.6154 |
| offline work saving | 38.46% |
| fallback count | 0 |
| exact reconvergence | observed in temporal-year view |

The first campaign attempt is retained under the `.failed-fixture-*` directory. It exposed and fixed a fixture contamination where the temporal mutation also changed entities; the corrected campaign has canonical equality for all 13 pairs.

## Interpretation and stop boundary

The provider-free theorem/reference obligations are green for the tested fixtures. The result is not yet a live algorithm-quality or wall-clock claim: the current implementation uses a deterministic parser and synthetic state views, and therefore has no LLM/Embedding/Graphiti calls to compare with B0.

The sealed Native anchor remains `B0_NATIVE_SERIAL`, artifact `d6e9e240c3ce`, with `T_B0 = 2,636.463018176 s` for the 30-episode 8B dual-replica prefix. B1 remains supplementary relaxed-order ceiling only. The currently running local service profile is 14B single-replica and is not resource-matched to that anchor; it is therefore excluded from headline comparisons.

Per the workplan, the next legal stage is a new matched-platform V7-FRESH live qualification. V7-INCREMENTAL treatment is not authorized until V7-FRESH quality/non-inferiority, frozen differential correctness, and online economics are independently sealed. No V6 scheduler or B1 autoresearch was resumed.
