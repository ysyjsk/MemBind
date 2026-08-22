# Offline Mechanism-Workload Audit

Date: 2026-08-22  
Scope: `saturated_fixed_work_baseline_v1_3`, formal baseline run
`sfwb-v1-3-formal-baseline-20260822-002`.

This is a new, independent audit record. It does not modify any sealed
artifact, does not start a service, and does not rerun construction or QA.

## Decision

`STOP_CURRENT_WORKLOAD_NOT_MECHANISM_ALIGNED`

The pinned Graphiti implementation contains genuine non-commutative,
state-dependent construction paths. The current four histories also show
out-of-order publication/state visibility and several real B0/B1 graph
differences. However, the artifacts do not isolate those differences as the
cause of unsafe parallel construction:

* source-0 extraction output changes even when the recorded prompt input
  metadata is the same;
* serial B0-A and B0-B already diverge on the same workload;
* exact extraction payloads, candidate identities/order, state versions, and
  resolution/effect decisions were not sealed;
* the only clear temporal active/inactive divergence is in `b6019101`, whose
  QA questions do not query the affected Chicago Bulls state;
* all 16 QA rows are labelled `knowledge-update`, but none is a controlled
  old-value/new-value, duplicate-resolution, contradiction, or invalidation
  challenge.

The current data therefore supports the weaker observations
`OBSERVED_GRAPH_DIVERGENCE`, `OBSERVED_OUT_OF_ORDER_STATE_VISIBILITY`, and
`CONCRETE_TEMPORAL_DIVERGENCE_IN_ONE_HISTORY`, but not the strong claim that
naive parallel construction is unsafe on this workload because of Graphiti's
non-commutative updates.

## Evidence index

* Pinned Graphiti source: `membind-validation/.venv/lib/python3.12/site-packages/graphiti_core/`.
* Formal canonical graphs and native traces:
  `artifacts/sfwb-v1-3-formal-baseline-20260822-002/blocks/`.
* QA inventory: `baseline_reuse_qa_analysis_20260819/expanded/expanded_qa_inventory.json`.
* Client settings: `FROZEN_CLIENT_CONFIG.json` (`temperature=0`, `top_p=1`,
  `seed=20260806`).
* Prior V5 telemetry limitation record:
  `artifacts/sfwb-v1-3-v5-first-divergence-20260821-001/`.

See `audit.json` for the machine-readable per-history classification.
