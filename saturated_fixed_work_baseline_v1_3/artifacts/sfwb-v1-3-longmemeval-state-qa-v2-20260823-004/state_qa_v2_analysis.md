# LongMemEval Layered Graph-Only QA v2

Decision: `STOP_B0_GRAPH_ANSWER_COVERAGE_INSUFFICIENT`

The existing strict temporal graph predicate is retained as a state
representation diagnostic. It is not the headline answer-accuracy
metric. Answer accuracy is scored only from the pinned official
LongMemEval Judge projection; missing/invalid Judge results are
unscored rather than incorrect.

| History | B0 answer | B1 answer | B0 state diagnostic | B1 state diagnostic |
|---|---|---|---|---|
| `07741c45` | `FAIL` | `FAIL` | `FAIL` | `FAIL` |
| `b6019101` | `FAIL` | `FAIL` | `NOT_PROVABLE` | `NOT_PROVABLE` |
| `6071bd76` | `FAIL` | `FAIL` | `FAIL` | `FAIL` |
| `a2f3aa27` | `FAIL` | `FAIL` | `FAIL` | `FAIL` |

B0 official answer accuracy: `0.0` (0/4).
B1 official answer accuracy: `0.0` (0/4).

All four B0 rows stop at graph evidence coverage: the Reader abstains
because the retrieved graph facts do not contain the official answer,
and the full canonical graphs also lack an active expected current edge.
This is a baseline/workload graph-coverage failure, not evidence of a
B1-only state race. No B0-pass/B1-fail pair exists.

Construction calls: `0`; Graph writes: `0`; V5 started: `false`.
