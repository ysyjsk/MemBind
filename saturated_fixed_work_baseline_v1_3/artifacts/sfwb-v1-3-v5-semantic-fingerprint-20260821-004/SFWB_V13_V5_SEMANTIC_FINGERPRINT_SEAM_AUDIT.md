# SFWB v1.3 V5 semantic fingerprint seam audit

Benchmark: `saturated_fixed_work_baseline_v1_3`

This audit is source review only. It does not import Graphiti, Neo4j, a model
client, or any runtime provider, and it does not modify the sealed artifacts.

## Native v3.1 call chain

The real `MemBindV31GraphitiAdapter` path is:

1. `paper_eval.membind_v31.graphiti_adapter.MemBindV31GraphitiAdapter.prepare`
   materializes the source/evidence, calls the pinned binding's
   `extract_nodes`, optionally calls `extract_edges`, and constructs
   `PreparedArtifact.create` from the returned runtime objects.
2. `MemBindV31GraphitiAdapter.bind` verifies and materializes that artifact,
   routes the group, retrieves latest state with Graphiti's existing
   `retrieve_episodes(..., group_ids=[source.group_id], source=...)`, then
   calls `resolve_extracted_nodes`.
3. The bind suffix calls `resolve_edge_pointers`,
   `resolve_extracted_edges`, `extract_attributes_from_nodes`, and finally
   `process_episode_data` (`Graphiti._process_episode_data`). The latter is
   the persistence/transaction boundary; publication is observed by the v4
   MEG wrapper after the durable commit.

## Pinned Graphiti 0.29.3 symbols

`paper_eval.s5_graphiti_semantic_binding.load_graphiti_semantic_binding`
binds exactly:

| boundary | pinned symbol | runtime object available to a passive observer |
| --- | --- | --- |
| node extraction | `graphiti_core.utils.maintenance.node_operations.extract_nodes` | returned node sequence and episode-index map |
| edge extraction | `graphiti_core.utils.maintenance.edge_operations.extract_edges` | returned edge sequence |
| node candidate read | internal `_collect_candidate_nodes` inside `resolve_extracted_nodes` | candidate rows are internal to the Graphiti call; not exposed by the v3.1 adapter |
| node resolution batch/decision | internal `_resolve_with_llm` and result tuple of `resolve_extracted_nodes` | result tuple is available to the adapter; batch membership and decision payload are not emitted to sealed telemetry |
| edge candidate read/decision | internal `resolve_extracted_edge` inside `resolve_extracted_edges` | child inputs/results are internal to the Graphiti call; not exposed by the v3.1 adapter |
| edge pointer resolution | `graphiti_core.utils.bulk_utils.resolve_edge_pointers` | returned pointer-edge sequence is available in `bind` |
| attribute/summary | `graphiti_core.utils.maintenance.node_operations.extract_attributes_from_nodes` | returned hydrated node sequence is available in `bind` |
| persistence | `graphiti_core.graphiti.Graphiti._process_episode_data` | commit return value is available; semantic effect identity is not serialized by the sealed native trace |
| publication | v4 MEG `SOURCE_PUBLICATION` recorder after transaction observation | publication event and source sequence are available |

## v1.3 benchmark persistence

`saturated_fixed_work_baseline_v1_3/membind_adapter.py` durably writes the
MemBind prepared document under `private/prepared/{sequence:08d}.json`, then
records lifecycle validation and publication. B0-A has no paired prepared
document, so this is not a cross-path extraction-output seam.

## Fingerprint boundary contract

The only newly added operation is passive canonical serialization of objects
that have already been returned by one of the exposed boundaries. It records
counts and SHA-256 digests of explicitly named semantic fields. It performs no
provider call, query, embedding, prompt construction, batching, admission, or
persistence operation. Runtime metadata such as namespace, run id, sequence,
timestamps, request ids, and object addresses is excluded and is rejected if
declared as semantic.

The following boundaries remain unavailable in the existing sealed traces and
must not be invented by analysis:

- `NODE_CANDIDATE_SET` identity/order and state-version-at-read:
  `NOT_OBSERVABLE_AT_THIS_BOUNDARY`
- `NODE_RESOLUTION_BATCH` membership and normalized decision:
  `NOT_OBSERVABLE_AT_THIS_BOUNDARY`
- `EDGE_CANDIDATE_SET` identity/order and child decision:
  `NOT_OBSERVABLE_AT_THIS_BOUNDARY`
- persistence effect identity/content and transaction-to-publication payload:
  `NOT_OBSERVABLE_AT_THIS_BOUNDARY`

The new helper therefore cannot retroactively upgrade the sealed v1.3 result;
it only defines the minimum passive telemetry contract for a future paired
diagnostic.
