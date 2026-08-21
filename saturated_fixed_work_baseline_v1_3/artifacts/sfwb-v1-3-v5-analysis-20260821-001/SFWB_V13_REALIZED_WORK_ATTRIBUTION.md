# SFWB v1.3 realized work attribution

| block | logical calls | attempts | input tokens | output tokens | embedding items | LLM service s |
| --- | --- | --- | --- | --- | --- | --- |
| B0-A | 255 | 256 | 717681 | 36095 | 572 | 1215.79733881 |
| B0-B | 255 | 256 | 717732 | 36118 | 572 | 1214.093982154 |
| B1 | 184 | 185 | 213288 | 32139 | 448 | 1152.356002782 |
| MemBind-v3.1 | 316 | 316 | 729048 | 23951 | 747 | 2338.378059058 |

## Prompt/operator reconstruction

### B0-A

| prompt | calls | input tokens | operator |
| --- | --- | --- | --- |
| dedupe_edges.resolve_edge | 106 | 92019 | EDGE_RESOLUTION |
| dedupe_nodes.nodes | 11 | 156372 | NODE_RESOLUTION |
| extract_edges.edge | 12 | 178065 | EDGE_EXTRACTION |
| extract_edges.extract_timestamps | 107 | 33450 | TIMESTAMP |
| extract_nodes.extract_message | 12 | 164819 | NODE_EXTRACTION |
| extract_nodes.extract_summaries_batch | 7 | 92956 | SUMMARY |

### B0-B

| prompt | calls | input tokens | operator |
| --- | --- | --- | --- |
| dedupe_edges.resolve_edge | 106 | 92070 | EDGE_RESOLUTION |
| dedupe_nodes.nodes | 11 | 156372 | NODE_RESOLUTION |
| extract_edges.edge | 12 | 178065 | EDGE_EXTRACTION |
| extract_edges.extract_timestamps | 107 | 33450 | TIMESTAMP |
| extract_nodes.extract_message | 12 | 164819 | NODE_EXTRACTION |
| extract_nodes.extract_summaries_batch | 7 | 92956 | SUMMARY |

### B1

| prompt | calls | input tokens | operator |
| --- | --- | --- | --- |
| dedupe_edges.resolve_edge | 73 | 67623 | EDGE_RESOLUTION |
| dedupe_nodes.nodes | 3 | 10041 | NODE_RESOLUTION |
| extract_edges.edge | 12 | 44634 | EDGE_EXTRACTION |
| extract_edges.extract_timestamps | 76 | 23833 | TIMESTAMP |
| extract_nodes.extract_message | 12 | 47243 | NODE_EXTRACTION |
| extract_nodes.extract_summaries_batch | 8 | 19914 | SUMMARY |

### MemBind-v3.1

| prompt | calls | input tokens | operator |
| --- | --- | --- | --- |
| dedupe_edges.resolve_edge | 138 | 121893 | EDGE_RESOLUTION |
| dedupe_nodes.nodes | 10 | 150657 | NODE_RESOLUTION |
| extract_edges.edge | 12 | 159043 | EDGE_EXTRACTION |
| extract_edges.extract_timestamps | 137 | 42485 | TIMESTAMP |
| extract_nodes.extract_message | 12 | 164819 | NODE_EXTRACTION |
| extract_nodes.extract_summaries_batch | 7 | 90151 | SUMMARY |

## Why 255 / 255 / 184 / 316?

B0-A and B0-B reproduce the same 255-call logical plan; their 51 input-token difference is token-payload variance with no sealed identity join, so it remains `UNKNOWN`, not an inferred plan change. B1 performs 71 fewer calls, principally fewer node resolutions (8), edge resolutions (33), and timestamps (31), while adding one summary call; the sealed trace shows branch-shape divergence but cannot prove which state mutation caused each omission. MemBind performs 61 more calls than B0-A: 32 additional edge resolutions and 30 additional timestamp calls, offset by one fewer node-resolution call. These prompt and span counts are observed; decomposition-created duplication and exact candidate-set causality remain `UNKNOWN` without a cross-policy operation identity.

Retry evidence: B0-A/B0-B/B1 each have retry overhead of one attempt above their logical calls; MemBind has zero retry overhead. Total attempt deltas also reflect the logical-call deltas and are reported separately in JSON. Embedding items are 572 / 572 / 448 / 747, and create/create_batch counts are preserved as measured native operations.

## Attribution status

- `changed_state/candidate_set_branch_divergence`: observed for resolution and timestamp call-count deltas, with causal mechanism explicitly unproven.
- `repeated_summary/attribute/timestamp_work`: observed for MemBind timestamp expansion.
- `different_batching_granularity`: observed only where summary-call counts differ; exact batch boundaries are unavailable.
- token-only changes with unchanged call count: `UNKNOWN`; no prompt payload identity join.
- `decomposition-created_duplicate_work`: `UNKNOWN`; no operation identity join.
- `instrumentation_artifact`: not observed; all counts cross-check sealed metrics.
