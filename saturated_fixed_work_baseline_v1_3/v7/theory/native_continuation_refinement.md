# Native Continuation Refinement

The candidate seam is after semantic construction and before the pinned
Graphiti `_process_episode_data` tail. The continuation-observable relation
`K` must include:

* logical episode/entity/edge IDs and the complete alpha bijection;
* ordered episode/node/edge collections and prompt-visible projections;
* effect/idempotency keys, endpoint UUIDs, group and temporal fields;
* embedder/model/schema/config epochs and clock/reference-time inputs;
* saga previous/current episode identifiers, NEXT_EPISODE and HAS_EPISODE
  targets; optional community work configuration;
* native read, oracle, backend and publication frontier versions;
* exception/retry branch inputs.

The source audit confirms that `_process_episode_data` calls the bulk helper
with an embedder and can execute saga get/create/query/save operations after
bulk. `add_nodes_and_edges_bulk_tx` can generate missing embeddings before
the transaction writes. Runtime task identity and coroutine completion timing
are not semantic unless they enter one of those fields.

Status is `UNKNOWN` until a source-level step-local proof closes this entire
K. The isolated reference model and final-state differential can expose a
refinement bug, but repeated final-state agreement cannot prove congruence.
Failure requires moving the seam, adding the missing observable, or blocking
M1/M2; it does not authorize a weaker alpha relation.
