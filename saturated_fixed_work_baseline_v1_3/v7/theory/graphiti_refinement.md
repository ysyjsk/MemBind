# Graphiti 0.29.3 P7 Refinement Audit

Audit input is the installed pinned Graphiti 0.29.3 source under
`membind-validation/.venv`; hashes are sealed in `P7_REFINEMENT_STATUS.json`.
This is a read-only source audit. It does not monkey-patch Graphiti and does
not authorize a treatment runtime.

## Operator status

| operator | status | evidence | guard/fallback |
|---|---|---|---|
| exact key/projection | SUPPORTED_WITH_GUARD | key/domain projection can be complete | missing-key phantom and projection fields -> UNKNOWN |
| node exact cosine top-k | SUPPORTED_WITH_GUARD | filtered full scan and exact cosine | query/filter/group/k/threshold/vector/index epochs and consumer tie order required |
| edge exact cosine top-k | SUPPORTED_WITH_GUARD | same exact scan structure | same guard; endpoint and edge projection fields included |
| BM25 | UNKNOWN | index/global statistics/analyzer/tie contract not sealed | always UNKNOWN/fresh |
| hybrid/RRF | UNKNOWN | BM25 channel and RRF tie order are not closed | always UNKNOWN/fresh |
| ANN | UNKNOWN | no backend proof in pinned contract | always UNKNOWN/fresh |
| previous episode retrieval | SUPPORTED_WITH_GUARD | selector result flows into node, edge and attribute extraction | selector/window/order/projection/digest is a required state dependency |
| `_process_episode_data` seam | UNKNOWN | bulk may embed before writes; saga can read/write afterward | M1 requires continuation K refinement; otherwise move seam/block |
| closed M2 Apply | UNSUPPORTED | embedding/bulk/saga reads are not a closed plan | M2 blocked; native continuation remains legal |
| live provider replay | UNKNOWN | V6 exact identity/single-consume is not a semantic provider contract | fresh response unless deployment declaration exists |

## Source facts

`_process_episode_data` calls `add_nodes_and_edges_bulk` with the embedder,
then may perform saga get/create, previous-episode lookup, NEXT_EPISODE and
HAS_EPISODE writes, and saga save. `add_nodes_and_edges_bulk_tx` generates
missing node/edge embeddings before the four bulk writes inside one
`execute_write` callback. Therefore this path is a native continuation/publish
seam candidate, not an already closed staged Apply.

Node and edge cosine search are filtered exact full scans. The source has no
secondary UUID tie contract. BM25, hybrid/RRF and ANN are consequently
UNKNOWN even if a sample happens to repeat.

## Scope rule

The selected exact-cosine region may be `SUPPORTED_WITH_GUARD` without
allowing an unrelated UNKNOWN operator to poison it. A missing relevant delta
field or ambiguous stable name still forces that region fresh.
