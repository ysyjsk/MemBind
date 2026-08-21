# SFWB v1.3 V5 first semantic divergence

Offline gate: `STOP_V5_FIRST_DIVERGENCE_INSUFFICIENT_OBSERVABILITY`

No new live diagnostic was run. Existing sealed telemetry contains no paired semantic fingerprint records, so the prior fail-closed conclusion remains authoritative.

| source | first boundary | classification | semantic cause provable | fingerprint boundary coverage |
| --- | --- | --- | --- | ---: |
| 0 | node_candidate_formation | OBSERVABILITY_INSUFFICIENT | FALSE | 0 |
| 1 | edge_extraction | OBSERVABILITY_INSUFFICIENT | FALSE | 0 |
| 2 | edge_extraction | OBSERVABILITY_INSUFFICIENT | FALSE | 0 |
| 3 | node_candidate_formation | OBSERVABILITY_INSUFFICIENT | FALSE | 0 |
| 4 | node_candidate_formation | OBSERVABILITY_INSUFFICIENT | FALSE | 0 |
| 5 | edge_extraction | OBSERVABILITY_INSUFFICIENT | FALSE | 0 |
| 6 | node_candidate_formation | OBSERVABILITY_INSUFFICIENT | FALSE | 0 |
| 7 | node_resolution_batch | OBSERVABILITY_INSUFFICIENT | FALSE | 0 |
| 8 | edge_extraction | OBSERVABILITY_INSUFFICIENT | FALSE | 0 |
| 9 | edge_extraction | OBSERVABILITY_INSUFFICIENT | FALSE | 0 |
| 10 | edge_extraction | OBSERVABILITY_INSUFFICIENT | FALSE | 0 |
| 11 | edge_extraction | OBSERVABILITY_INSUFFICIENT | FALSE | 0 |

The request/token and span/cardinality signals remain fallback observations only. Final graph differences were not used for causality.
