# DMSV B1R2 native localization audit

Graphiti 0.29.3's MemBind adapter first collects candidates for each extracted
node, merges unresolved candidates into one ordered union, constructs a single
context containing `previous_episodes`, the current episode, extracted nodes and
existing-node payloads, and invokes `dedupe_nodes.nodes` once. The response is a
joint `NodeResolutions.entity_resolutions` list indexed over the unresolved
batch.

This boundary exposes no native per-node response binding, partial-batch
replay seam, or proof that one changed entity can be removed from the joint
prompt while preserving the original algorithm and response contract.

Therefore a changed canonical request is classified as `REQUEST_DIRTY`. A
per-node split, removal of previous context, prompt rewrite, schema rewrite or
assumed response equivalence would be `NEW_ALGORITHM_LOCALIZABLE`, not a detail
of Frozen V6 Native localization. Since the B1R2 theorem is only conditional and
no complete eligible population has been established, the audit reports
`NATIVE_LOCALIZATION_STATUS=UNPROVEN` and does not emit a structural NULL.

No provider, database or scheduler action was performed.
