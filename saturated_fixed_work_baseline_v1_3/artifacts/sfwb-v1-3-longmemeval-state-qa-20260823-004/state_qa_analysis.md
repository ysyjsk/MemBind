# LongMemEval Graph-Only State QA Analysis

Decision: `STOP_LONGMEMEVAL_B0_STATE_PREDICATE_INELIGIBLE`

Scope is the four already completed B0/B1 graph pairs only. The raw
operation freeze still contains 72 structural LongMemEval-S cases; no
new construction was run in this lane.

The direct temporal graph predicate is authoritative. Reader output from
8002 is diagnostic only and cannot turn a missing graph current fact into
a PASS. Graphiti search used the guarded read-only path with embedding 8003.

| History | B0 state | B1 state | First boundary |
|---|---|---|---|
| `07741c45` | `FAIL` | `FAIL` | `NO_ACTIVE_EXPECTED_CURRENT_EDGE` |
| `b6019101` | `NOT_PROVABLE` | `NOT_PROVABLE` | `EXPECTED_TOKEN_ONLY_UNRELATED_TO_QUESTION_STATE` |
| `6071bd76` | `FAIL` | `FAIL` | `NO_ACTIVE_EXPECTED_CURRENT_EDGE` |
| `a2f3aa27` | `FAIL` | `FAIL` | `NO_ACTIVE_EXPECTED_CURRENT_EDGE` |

No history has `B0 PASS -> B1 FAIL`; no direct graph-state divergence
was established. The 8002 Reader matched the official answer on 0/8
rows, which confirms that retrieval/Reader output is not a valid
substitute for current-state semantics here.

The result does not authorize a B1 unsafe claim, a 72-history live
expansion, scheduler work, or V5.
