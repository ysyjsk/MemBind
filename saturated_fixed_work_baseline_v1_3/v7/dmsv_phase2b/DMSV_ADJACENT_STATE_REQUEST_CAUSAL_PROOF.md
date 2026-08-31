# DMSV adjacent-state request causal proof

Date: 2026-08-31
Input commit: `58a925f372db1a095c9e90b969ad74d101c4e96a`
Graphiti: `0.29.3`

## Claim boundary

The provider-free delta matrix is a sensitivity experiment: changing an input
field changes the canonical `dedupe_nodes.nodes` prompt. It is not evidence that
the same field changes in a real state transition. The stronger terms used here
are deliberately separated:

| Level | Required evidence | Result |
|---|---|---|
| Sensitivity | controlled field mutation changes request digest | `PASS_PROVIDER_FREE` (historical matrix) |
| Inevitability | field changes in an actual adjacent authoritative pair | observed pair, but incomplete binding witness |
| Unavoidability | inevitability plus no legal native batch localization | not established |

## Actual Graphiti chain audited

The installed Graphiti source exposes the following chain:

`Graphiti.add_episode -> retrieve_episodes -> resolve_extracted_nodes -> _resolve_with_llm -> prompt_library.dedupe_nodes.nodes`.

`retrieve_episodes` filters `e.valid_at <= $reference_time`, optionally filters
`group_id` and `source`, orders by `e.valid_at DESC`, applies
`LIMIT $num_episodes` (with `num_episodes=last_n`), and returns the selected
episodes in chronological order. MemBind serializes each retrieved episode's
`content` and `valid_at` into `previous_episodes` before invoking the real
prompt builder.

## Development observer witness

The non-held-out observer artifact
`DVSR_CROSS_SNAPSHOT_OBSERVER.jsonl` contains a source-4 pair for history
`b6019101`:

- authoritative state version changes from 3 to 4;
- the previous-episode order grows from three IDs to four IDs;
- the previous projection digest changes from
  `087043fddeaf0101ef969eb87544c7a723aef1486cd2b4f9c8a575d76fb5aa1c` to
  `91fecade6ce358d39ca2381d5f34e35863660ed5c1df9e196460e301526b773b`;
- the recorded `dedupe_nodes.nodes` request identity changes from
  `ae0dda4acb8b8d090b6b40455c5ca77aee95c7b8eb224aa07b68ee6f5ef4820c` to
  `026d19548eccd5883dfb375494f33b3e4648fdbf3706fbcdf7df3aca9c41c974`;
- the recorded prompt-message digest changes from
  `ee192e739e14b403c9cd89ae9302526b19516f20e31ab2fee9c93c8898a1c2bb` to
  `cb6364ed0c4737d32772449ec6f8c9346fff73b848cf2fc3105bc681695e53bd`.

This is a useful real adjacent-state signal, but the observer does not retain
the complete retrieval reference time, group/source binding, `last_n`, an
independent request-binding digest, schema/index epochs, or decoding contract.
Those fields cannot be reconstructed without inventing evidence. The witness is
therefore recorded as `REAL_PAIR_WITNESS_MISSING_FIELD` in
`DMSV_DOMINANT_REQUEST_CAUSAL_WITNESSES.jsonl`.

## Verdict

`FINAL_STATE=BLOCKED_DOMINANT_REQUEST_INEVITABILITY_UNPROVEN`.

The historical sensitivity matrix remains valid for its narrow claim. The real
pair does not yet justify the stronger `DMSV_NATIVE_NODE_NULL_DOMINANT_CALL_ALWAYS_DIRTY`
or `DMSV_DOMINANT_CALL_UNAVOIDABLE` conclusion. No batch splitting, provider call,
authoritative write, scheduler change, or live experiment is authorized by this
correction.
