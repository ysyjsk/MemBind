# Migration Map

The source of truth for the new mainline is `clean_membind/src/membind`. Old
directories are preserved as legacy/evidence and are not imported by the clean
package.

| Clean boundary | Extracted contract | Legacy source | Deliberately excluded |
| --- | --- | --- | --- |
| `core/contracts.py` | request identity, prepared payload, validation, one-shot store | `saturated_fixed_work_baseline_v1_3/membind_v6_1/identity.py`, `membind_adapter.py` | Graphiti/runtime imports |
| `core/scheduler.py` | future preparation, ordered frontier, reuse/fallback | `membind_v6_1/executor.py`, `membind_v6_1/resource_credit.py` | qualification gates and adaptive policy search |
| `native/graphiti_adapter.py` | episode mapping and direct `Graphiti.add_episode` | `mab_live_runner.py`, `membind_v5/live_runner.py` | prompt changes, JSON repair, finite-pair patch |
| `native/async_baseline.py` | same Native with bounded concurrency | old async arm in `mab_live_runner.py` | extra model or relaxed schema |
| `workload/mab.py` | role-aware lossless 8192-character chunks | `mab_quality_v2_final_qa/mab8192_adapter.py` | QA labels in construction |
| `backends/ollama_qwen25.py` | frozen model/runtime/Graphiti identity | old backend manifests and external deployment docs | Qwen3/vLLM P2 routing |
| `governance/identity.py` | implementation hash | old identity/seal scripts | multi-layer authorization state machine |
| `governance/telemetry.py` | append-only fsynced events | old resource evidence writers | provider-specific retry accounting |
| `experiment/*` | smoke, cell envelope, reducer hook | old qualification/finalizer scripts | L1/L2/G0-G5 framework |

## Legacy-only inventory

`saturated_fixed_work_baseline_v1_3/`, `membind-validation/`, `paper-eval-v3/`,
V5/V6/V6.1/V7 modules, Qwen3/vLLM launchers, and all existing experiment
artifacts remain untouched. They can be cited or replayed for historical
analysis, but a clean run cannot import them or reuse their namespaces.

## Non-goals

This migration does not rewrite Graphiti, alter LongMemEval semantics, change
the frozen source order, or claim that a local model is reliable before the
three validation steps in `MAIN_EXPERIMENT_PLAN.md` complete.
