# MemOps B0 Small Screening

`SMALL_SCREENING_PARTIAL`

This append-only table covers only the completed prefix of the sealed 59-sample cohort. B1/V5 were not started and the qualified subset is not frozen.

| Ordinal | Sample | QA | Current state | Publication | Read-only | Failure boundary |
|---:|---|---:|---|---|---|---|
| 1 | `A01__Update` | 2/2 | PASS | True | writes=0, mutated=False | `NONE` |
| 2 | `A05__Update` | 2/2 | PASS | True | writes=0, mutated=False | `NONE` |
| 3 | `A13__Update` | 2/2 | PASS | True | writes=0, mutated=False | `NONE` |
| 4 | `A14__Update` | 2/2 | PASS | True | writes=0, mutated=False | `NONE` |
| 5 | `A28__Update` | 2/2 | FAIL | True | writes=0, mutated=False | `NATIVE_GRAPHITI_EXTRACTION_RECALL` |

Completed: 5; interrupted before completion: 54.

A current-state failure is distinct from official QA correctness. Reader QA may be correct while canonical graph state fails the stricter current-state inspection.
