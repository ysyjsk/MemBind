# SFWB v1.3 V5 first-divergence analysis

## Decision

`STOP_V5_FIRST_DIVERGENCE_INSUFFICIENT_OBSERVABILITY`

The analysis does not use final graph differences to infer a cause. It aligns the same 12 source hashes through request/span-level stages and records the earliest observable request-shape or candidate-cardinality signal. A signal is not promoted to a semantic cause unless paired payload, output, candidate identity, state version, and batch lineage are present; those fields are absent from the sealed telemetry.

## Per-source earliest observable signal

| source | first observable stage | signal | FIRST_PROVABLE_DIVERGENCE | edge-resolution delta | timestamp delta |
| --- | --- | --- | --- | ---: | ---: |
| 0 | node_candidate_formation | candidate_span_or_cardinality | OBSERVABILITY_INSUFFICIENT | 0 | 0 |
| 1 | edge_extraction | input_token_vector | OBSERVABILITY_INSUFFICIENT | 0 | 4 |
| 2 | edge_extraction | input_token_vector | OBSERVABILITY_INSUFFICIENT | 7 | 7 |
| 3 | node_candidate_formation | candidate_span_or_cardinality | OBSERVABILITY_INSUFFICIENT | 0 | 0 |
| 4 | node_candidate_formation | candidate_span_or_cardinality | OBSERVABILITY_INSUFFICIENT | 0 | 0 |
| 5 | edge_extraction | input_token_vector | OBSERVABILITY_INSUFFICIENT | -8 | -8 |
| 6 | node_candidate_formation | candidate_span_or_cardinality | OBSERVABILITY_INSUFFICIENT | 5 | 5 |
| 7 | node_resolution_batch | input_token_or_call_vector | OBSERVABILITY_INSUFFICIENT | 0 | -6 |
| 8 | edge_extraction | input_token_vector | OBSERVABILITY_INSUFFICIENT | 33 | 33 |
| 9 | edge_extraction | input_token_vector | OBSERVABILITY_INSUFFICIENT | 8 | 8 |
| 10 | edge_extraction | input_token_vector | OBSERVABILITY_INSUFFICIENT | 3 | 3 |
| 11 | edge_extraction | input_token_vector | OBSERVABILITY_INSUFFICIENT | -16 | -16 |

## Causal interpretation

The aggregate `+32 EDGE_RESOLUTION` and `+30 TIMESTAMP` calls are real downstream fan-out observations. The sealed traces do not prove that they are duplicate consumption, and do not prove which earlier extraction, state snapshot, candidate set, or batch decision caused them. Several sources show an earlier edge-extraction input-token mismatch; others first show node-candidate cardinality or node-resolution request-shape differences. Source 0 has equal extraction token vectors and only a candidate-span-shape difference. These are distinct observations, not a single proven root cause.

The minimum observed Compile/Bind boundary is the point where v3.1 request planning no longer exposes the Native Serial semantic path as a paired operator lineage: prepared outputs exist only for MemBind, and B0 has no matching artifact. For sources with differing logical request order, the ordering difference is observable; its semantic effect is not provable.

## Required observability before mechanism design

1. Paired canonical extraction output digests and cardinalities.
2. Exact prompt/input hashes and batch IDs on both paths.
3. Ordered candidate-set identity, state version, and resolution decision digest.
4. Effect/mutation identity and publication-version lineage.

No runtime mechanism, scheduler, admission change, or live retry is authorized by this artifact.
