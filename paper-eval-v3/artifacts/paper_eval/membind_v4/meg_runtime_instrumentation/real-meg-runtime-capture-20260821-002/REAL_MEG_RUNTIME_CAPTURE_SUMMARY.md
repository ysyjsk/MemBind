# Real MEG Runtime OBSERVE_ONLY Capture Summary

STATUS: STOP_REAL_RUNTIME_SEMANTIC_LINEAGE
FAILURE_CLASSIFICATION: B_SEMANTIC_LINEAGE_FAILURE
RUN_ID: membind-v31-opt-w4-meg-runtime-observe-20260821-002
HISTORY: 07741c45
SOURCES: [0, 1, 2]
NAMESPACE: pev3-opt-membind-v31-w4-meg-runtime-observe-20260821-002-07741c45
GRAPHITI_VERSION: 0.29.3
SEAM_HASH: edbb7321ce10f50ee0ede913fe9f5d80fcd236377bdc074c321e1f487e703aab

## Source Completion

Source 0 reached compile, prepared-durable, and bind-start, then failed in bind. Sources 1 and 2 did not complete.
No source reached durable publication.

## Semantic Lineage

Semantic operators before failure: 3
OPERATOR_READY count: OPAQUE (complete capture payload was not materialized)
Request spans before failure: 2
Request lineage coverage: OPAQUE
Opaque lineage count: 0

## State / Transaction

Transaction commits observed: 0
Mutation epoch transitions observed: 0
Writer domain after failed capture: OPAQUE_UNOBSERVED

## Publication

Publication events observed: 0
Publication causal coverage: OPAQUE

## Passive Equivalence

NOT_CERTIFIED: the partial failed execution cannot prove equality against the v3.1 baseline.
Shadow DB reads: 0; shadow LLM calls: 0; shadow embeddings: 0; extra writes: 0; reorder: no.

## Final Decision

STOP_REAL_RUNTIME_SEMANTIC_LINEAGE
No live retry, source expansion, or SHADOW_READ is authorized by this artifact.
