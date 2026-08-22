# MemOps State Attack QA Analysis

Status: `EXPLORATORY_STATE_DIVERGENCE_OBSERVED`

The authoritative result is the direct temporal graph-state predicate. The model substring comparator is diagnostic only and is not treated as semantic correctness.

Paired samples: `20`
B0 PASS -> B1 FAIL: `A01__Update, C21__Update, D02__Update`
B0 FAIL -> B1 PASS: `B03__Update, B21__TrajectoryOps, B26__TrajectoryOps`

| Sample | B0 state | B1 state |
|---|---|---|
| A01__Update | PASS | FAIL |
| A05__Update | FAIL | FAIL |
| A13__Update | PASS | PASS |
| A14__Update | PASS | PASS |
| A28__Update | FAIL | FAIL |
| A29__Update | FAIL | FAIL |
| A33__TrajectoryOps | PASS | PASS |
| A33__Update | FAIL | FAIL |
| B01__Update | PASS | PASS |
| B02__Update | PASS | PASS |
| B03__Update | FAIL | PASS |
| B10__Update | PASS | PASS |
| B21__TrajectoryOps | FAIL | PASS |
| B25__TrajectoryOps | FAIL | FAIL |
| B26__TrajectoryOps | FAIL | PASS |
| C21__Update | PASS | FAIL |
| C30__Update | FAIL | FAIL |
| D02__Update | PASS | FAIL |
| E15__Update | FAIL | FAIL |
| F17__Update | PASS | PASS |

The challenge used graph-fact-only reader context with the alternate Qwen endpoints `8002/8003`. It performed zero construction calls and zero graph writes.

This is not an official MemOps qualification result and does not by itself establish the full unordered-admission causal chain.
