# MSEG Operator Audit

## Scope and Binding

This is a read-only audit of the installed Graphiti 0.29.3 production
`Graphiti.add_episode` path and the sealed v3.1 W=4 pilot for
`history=07741c45`, sources `0..11`. It starts no service, sends no model or
embedding request, creates no namespace, and performs no persistent write.

Graphiti source hashes:

```json
{
  "metadata_file_sha256": "f9ef708c33fc91df9fd9015c9dd7694d4d18a1f706ce63598c6176364b6aa3dc",
  "source_file_sha256s": {
    "bulk_utils.py": "6c7314f24801f0936454b3344788528500432ac5f12692eb36b7d3ef5269f601",
    "edge_operations.py": "b773ff4489968af2a996d5074e679cab9806cc0904a7ff9f2aecc74382325abe",
    "graphiti.py": "7c65051a62982d8b510ebdbf37bae4d07020e74520e1f6d9bf8a0ffb26beeccb",
    "node_operations.py": "14fc92a462bf7f1dd9b70d10a88e27e36a0ddc1594dc18381888209de7137fb4"
  },
  "version": "0.29.3"
}
```

## Production Path

The code-proven control flow is:

```text
previous-episode read
  -> episode materialization
  -> EntityExtract
  -> NodeCandidateLookup
  -> deterministic NodeResolve, then conditional dedupe_nodes.nodes
  -> EdgeExtract
  -> EdgePointerRemap
  -> EdgeCandidateLookup
  -> deterministic/LLM EdgeResolve
  -> conditional Temporal and EdgeAttribute extraction
  -> deterministic invalidation
  -> conditional EntityAttribute and batched Summary work
  -> embeddings and episodic-effect construction
  -> add_nodes_and_edges_bulk transaction
  -> publication complete
```

`update_communities` defaults to false and is outside the frozen primary path.
The audit follows actual conditional code: a possible role is not asserted to
occur in every episode.

## Code-Proven Operator Surface

| Operator role | Caller | Callee | Execution | Persistent read | Persistent write | Blocks publication |
|---|---|---|---|---:|---:|---:|
| PreviousEpisodeLookup | Graphiti.add_episode | Graphiti.retrieve_episodes / EpisodicNode.get_by_uuids | DETERMINISTIC_DB_READ | True | False | True |
| EpisodeMaterialize | Graphiti.add_episode | EpisodicNode.get_by_uuid or local EpisodicNode constructor | CONDITIONAL_DB_READ_OR_LOCAL | CONDITIONAL | False | True |
| EntityExtract | Graphiti.add_episode | extract_nodes -> _call_extraction_llm | LLM_REQUEST | False | False | True |
| NodeCandidateLookup | resolve_extracted_nodes | _collect_candidate_nodes -> node_similarity_search | EMBEDDING_AND_DETERMINISTIC_DB_READ | True | False | True |
| NodeResolveDeterministic | resolve_extracted_nodes | _resolve_with_similarity | DETERMINISTIC | False | False | True |
| NodeResolveLLM | resolve_extracted_nodes | _resolve_with_llm | CONDITIONAL_LLM_REQUEST | False | False | True |
| EdgeExtract | Graphiti._extract_and_resolve_edges | extract_edges | LLM_REQUEST | False | False | True |
| EdgePointerRemap | Graphiti._extract_and_resolve_edges | resolve_edge_pointers | DETERMINISTIC | False | False | True |
| EdgeCandidateLookup | resolve_extracted_edges | EntityEdge.get_between_nodes and search | EMBEDDING_AND_DETERMINISTIC_DB_READ | True | False | True |
| EdgeResolveLLM | resolve_extracted_edges | _resolve_extracted_edge | CONDITIONAL_LLM_REQUEST | False | False | True |
| TemporalExtract | _resolve_extracted_edge | _extract_edge_timestamps | CONDITIONAL_LLM_REQUEST | False | False | True |
| EdgeAttributeExtract | _resolve_extracted_edge | extract_edges.extract_attributes prompt path | CONDITIONAL_LLM_REQUEST | False | False | True |
| EdgeInvalidation | _resolve_extracted_edge | resolve_edge_contradictions and timestamp comparisons | DETERMINISTIC | False | False | True |
| EntityAttributeExtract | extract_attributes_from_nodes | _extract_entity_attributes | CONDITIONAL_LLM_REQUEST_PER_NODE | False | False | True |
| EntitySummary | extract_attributes_from_nodes | _extract_entity_summaries_batch | CONDITIONAL_BATCHED_LLM_OR_DETERMINISTIC_APPEND | False | False | True |
| EntityEmbedding | extract_attributes_from_nodes | create_entity_node_embeddings | EMBEDDING_REQUEST | False | False | True |
| EpisodicEffectBuild | Graphiti._process_episode_data | build_episodic_edges and local effect materialization | DETERMINISTIC | False | False | True |
| PersistentWritePublication | Graphiti._process_episode_data | add_nodes_and_edges_bulk -> add_nodes_and_edges_bulk_tx | DETERMINISTIC_WRITE_TRANSACTION_WITH_EMBEDDING_GUARDS | False | True | True |

This table proves possible production operators and their control/dataflow
position. It does not prove per-request instances, timing, memory versions, or
effect scopes in the sealed pilot.

`Blocks publication` means the current production control flow awaits that
invoked operation before the bulk write can return. It does not mean the
operation is on a measured publication critical path. Critical-path membership
and slack require instance-level dependency and timing evidence, which is
`NOT_OBSERVABLE` here.

Prompt roles proven in Graphiti include `extract_nodes.extract_message`,
`dedupe_nodes.nodes`, `extract_edges.edge`, `dedupe_edges.resolve_edge`,
`extract_edges.extract_timestamps`, `extract_edges.extract_attributes`,
`extract_nodes.extract_attributes`, and
`extract_nodes.extract_summaries_batch`. These strings exist at the live
`generate_response(..., prompt_name=...)` call sites.

## Trace-Observed Instances

The sealed request trace directly observes 279 requests: 24 COMPILE and 255
FRONTIER. All 279 have complete client submit/start/terminal lifecycles.

It does **not** persist `prompt_name`, `operator_role`, `operator_id`, parent
operator/Bind identity, operator ready/materialization timestamps, exact memory
version, publication frontier, dependency edges, read/effect scope,
deterministic operator instances, persistent effects, or publication instances.

Fields with zero direct coverage:

```text
admission_enqueue_ns
completion_tokens
data_dependency_ids
dependency_knowledge_state
effect_conflict_dependency_ids
effect_scope
episode_arrival_ns
execution_mode
history_id
memory_version_observed
memory_version_required
operator_end_ns
operator_id
operator_ready_ns
operator_role
operator_start_ns
parent_bind_id
parent_operator_id
prompt_name
prompt_tokens
publication_dependency_ids
publication_frontier_at_materialization
publication_frontier_at_ready
read_scope
request_materialized_ns
version_dependency_ids
```

Therefore trace-observed fine-grained operator instances are
`NOT_OBSERVABLE`. The role count and same-role width are also
`NOT_OBSERVABLE`.

## Classification Boundary

No role is inferred from request order, prompt/token length, prefix/cache hash,
or prompt similarity. `client running` is neither vLLM batch membership nor GPU
execution. The older `ROLE_PROFILE.json` is an initialization reference from a
different logical-call trace and cannot attribute these W=4 requests.

The current wrapper receives Graphiti keyword arguments at
`AdmittedLLMClientV31._execute`, but the frozen event projection intentionally
omits `prompt_name`. Modifying the frozen v3.1 runtime or running a new
instrumented candidate is outside this Oracle Gate.

## Audit Decision

The production operator *surface* is recovered from code. The target pilot's
operator *instances and causal graph* are not recoverable. A scientific MSEG,
late-bound analysis, conflict oracle, publication critical path, and O1-O4
comparison cannot be constructed without fabricating evidence.
