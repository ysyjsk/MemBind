# Native Continuation Refinement

## Selected seam and status

The selected seam is immediately before pinned Graphiti 0.29.3
`_process_episode_data`. Its status is `SUPPORTED_WITH_GUARD` for the default
MemBind construction profile only:

* `saga is None` and `saga_previous_episode_uuid is None`;
* `update_communities is False`;
* every entity node has `name_embedding` and every entity edge has
  `fact_embedding` before the seam;
* provider is Neo4j and the database/schema epoch and publication frontier
  are sealed;
* the complete `membind.v7.graphiti-continuation-k.v1` record validates.

Any failed or missing guard gives `UNKNOWN` and forces the original native
path. Saga/community continuations are not covered by this proof. Closed M2
Apply remains `UNSUPPORTED`.

## Continuation-observable K

`K` compares all fields exactly, including ordered collections and IDs:

* complete episode payloads and ordered episode UUIDs;
* complete entity node and entity edge publication payloads, including
  embeddings, attributes, temporal fields and ordered provenance episodes;
* edge source/target UUIDs, node-to-episode index mapping and group/database;
* `now`, raw-content policy, backend/schema epoch and publication frontier;
* saga/community controls, even though the selected guard requires them off.

Runtime task identity and coroutine completion order are absent. Endpoint
UUIDs are not erased by ordinary alpha canonicalization because the native
tail consumes them as effect targets.

## Step-local source proof

For the exact files sealed in `continuation.PINNED_CONTINUATION_SOURCE_HASHES`:

1. `_process_episode_data` normalizes one episode to an ordered list, derives
   ordered episode UUIDs and calls `build_episodic_edges(nodes, episode_uuids,
   now, node_episode_index_map)`.
2. `build_episodic_edges` depends only on the ordered node UUIDs/group IDs,
   episode UUID sequence, `now` and the index map in `K`. Its newly minted edge
   UUID is alpha-renamable; its endpoints and order are not.
3. The tail mutates only `episode.entity_edges` and, under the captured raw
   content policy, `episode.content` before publication. Both results are
   functions of `K`.
4. The bulk helper calls the embedder only when a node/edge embedding is
   missing. The guard excludes those branches, so no oracle call remains.
5. Neo4j executes episode, entity-node, episodic-edge and entity-edge writes
   in one `execute_write` callback. The pinned queries match/MERGE by UUID and
   set only payload fields captured by `K`.
6. The saga read/write block is controlled by `saga is not None`; the guard
   excludes `_get_or_create_saga`, previous-episode lookup, NEXT_EPISODE,
   HAS_EPISODE and saga save.
7. Community read/LLM/embed/write work occurs after `_process_episode_data`
   only under `update_communities`; the guard excludes it.
8. Logging, tracing and elapsed-time attributes do not influence returned
   semantic objects or writes. Exceptions abort the native call; transaction
   retry repeats UUID-keyed MERGE work against the same captured payload.

Therefore two isolated authoritative states at the same frontier/schema epoch
and two seam records equivalent under exact `K` take the same branches and
issue alpha-equivalent native effects. This discharges A16/T6b for this guard,
not for arbitrary Graphiti continuations.

## Executable obligations

`membind_v7.continuation` validates the full guard and exact `K` equality.
Tests reject missing embeddings, saga/community work, endpoint-ID changes and
frontier changes. `audit_continuation_source` binds the proof to seven pinned
source hashes; any drift returns `UNKNOWN`.

Frozen differential tests remain required in R5 after a method is selected.
Empirical agreement is a refinement falsifier, not the proof above.
