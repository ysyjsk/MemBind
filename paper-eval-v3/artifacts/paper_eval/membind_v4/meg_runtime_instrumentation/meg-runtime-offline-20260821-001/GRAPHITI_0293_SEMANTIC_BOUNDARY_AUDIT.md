# Graphiti 0.29.3 Semantic Boundary Audit

This audit is pinned to the installed Graphiti 0.29.3 source used by the MemBind experiment environment.

## Source Identity

- version: `0.29.3`
- source root: `/data/predator/ly/MemBind/membind-validation/.venv/lib/python3.12/site-packages/graphiti_core`
- publication contract: `GRAPHITI_0293_ADD_EPISODE_SAGA_FREE_V0`
- write-path coverage: `33/33` (`1.0`)
- audit status: `PASS`

| Source | SHA-256 |
| --- | --- |
| `bulk_utils.py` | `6c7314f24801f0936454b3344788528500432ac5f12692eb36b7d3ef5269f601` |
| `edge_operations.py` | `b773ff4489968af2a996d5074e679cab9806cc0904a7ff9f2aecc74382325abe` |
| `graph_data_operations.py` | `ab5d375738fdd5e8a3aa39242d8dc9b7b281dd0bedb05cd8a7659548582106cb` |
| `graphiti.py` | `7c65051a62982d8b510ebdbf37bae4d07020e74520e1f6d9bf8a0ffb26beeccb` |
| `neo4j_driver.py` | `e7043f0409ddb825718a5fd7e758e527fe74b507de72d1e91bc452bcccbe7395` |
| `node_operations.py` | `14fc92a462bf7f1dd9b70d10a88e27e36a0ddc1594dc18381888209de7137fb4` |

## Semantic Boundaries

| Operator | Semantic inputs | Mutable state read | LLM | Private result | Persistent mutation | Completion semantics | Publication impact | Classification | ReadView | Source evidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| NodeExtraction | immutable source episode plus certified immutable evidence prefix | False | True | True | False | extracted EntityNode list and episode attribution map materialized | False | EVIDENCE_DERIVED | False | v3.1 graphiti_adapter.prepare; node_operations.extract_nodes |
| NodeCandidateRead | extracted names, embeddings, group, candidate limit and cosine threshold | True | False | True | False | all ordered per-node candidate lists returned | False | STATE_DERIVED | True | node_operations._semantic_candidate_search:418-450 |
| DeterministicSimilarity | one extracted node and its materialized candidate index | False | False | True | False | exact/similarity decision committed to private batch state | False | DERIVED_PRIVATE | False | node_operations.resolve_extracted_nodes:649-670 |
| UnresolvedSetFormation | private deterministic decisions in extracted-node order | False | False | True | False | complete unresolved index set materialized | False | DERIVED_PRIVATE | False | node_operations.resolve_extracted_nodes:649-680 |
| NodeBatchResolutionDecision | all unresolved nodes, merged ordered mutable candidates, episode and previous episodes | True | True | True | False | one multi-input NodeResolutions response applied to private batch state | False | STATE_DERIVED | True | node_operations._resolve_with_llm:467-624 |
| IdentityMaterialization | private batch resolution state | False | False | True | False | resolved nodes, UUID map and duplicate pairs returned | False | DERIVED_PRIVATE | False | node_operations.resolve_extracted_nodes:691-708 |
| EdgeExtraction | immutable source/evidence plus resolved private node identity | False | True | True | False | extracted edge list materialized | False | DERIVED_PRIVATE | False | v3.1 graphiti_adapter.prepare; edge_operations.extract_edges consumes the prior resolved-node result and performs no persistent read |
| EdgeCandidateRead | endpoint lookup, hybrid duplicate search and graph-wide invalidation search | True | False | True | False | ordered duplicate and invalidation candidates materialized per edge | False | STATE_DERIVED | True | edge_operations.resolve_extracted_edges:360-486 |
| EdgeResolutionChild | one extracted edge plus its exact ordered candidate ReadView | True | True | True | False | dedupe, optional attribute and timestamp subrequests plus contradictions complete | False | STATE_DERIVED | True | edge_operations.resolve_extracted_edges:488-509; resolve_extracted_edge:623-847 |
| NodeAttributeSummaryBatch | resolved node mutable fields, previous episodes and new edge facts | True | True | True | False | attributes, summaries and node embeddings materialized | False | STATE_DERIVED | True | node_operations.extract_attributes_from_nodes:726-780 |
| PersistAndPublish | hydrated nodes, resolved/invalidated edges and episode attribution | False | False | False | True | managed bulk transaction returns successfully | True | PERSISTENT_EFFECT | False | graphiti._process_episode_data:680-735; bulk_utils:128-148 |
| SourcePublication | successful saga-free managed bulk transaction commit evidence | False | False | False | False | source becomes durable at that commit; later saga/community writes forbidden in v0 | True | PUBLICATION | False | v3.1 process call passes saga=None; update_communities path absent |

## Attribute, Timestamp, And Summary Classification

| Operator | Mutable state read | Evidence | Classification | ReadView required | Covered by parent ReadView |
| --- | --- | --- | --- | --- | --- |
| edge attribute subrequest | False | resolve_extracted_edge receives the parent child's already materialized resolved edge | DERIVED_PRIVATE | False | True |
| edge timestamp subrequest | False | _extract_edge_timestamps consumes fact plus immutable episode reference time | DERIVED_PRIVATE | False | True |
| node attribute subrequest | True | resolved existing node attributes and latest previous episodes enter the prompt | STATE_DERIVED | True | False |
| node summary batch | True | resolved summaries/attributes and latest previous episodes enter the batch prompt | STATE_DERIVED | True | False |

## Transaction And Publication Boundary

`add_nodes_and_edges_bulk()` calls `session.execute_write()`, so the commit observer is placed on successful managed-transaction return. Callback retries do not advance the mutation epoch; only the successful outer return does.

The certified MEG-v0 path requires `saga=None` and no community update. Saga and community paths remain `CONFIG_GUARDED_OUT_OF_SCOPE`; they are not silently treated as covered production paths.

No live service was contacted while generating this audit.
