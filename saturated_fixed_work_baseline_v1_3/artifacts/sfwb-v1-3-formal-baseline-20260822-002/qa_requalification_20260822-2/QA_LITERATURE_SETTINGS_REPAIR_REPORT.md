# QA Literature Settings And Repair Report

## Scope

This is a read-only QA requalification over the eight namespaces sealed by
`sfwb-v1-3-formal-baseline-20260822-002`. Construction was not rerun, and the
original `qa/` artifacts were not modified. The new evidence is under this
directory only.

## Protocol comparison

The pinned Quality Evaluation v1 contract uses Graphiti 0.29.3 Edge BM25 plus
cosine similarity with RRF, Episode BM25 with RRF, Top-20 results, deterministic
context assembly, the frozen Qwen Reader, and the frozen Qwen Judge. The
earlier S2-R0 probe is a different contract: Episode-only BM25/RRF, Top-10,
and no embedding, Reader, or Judge calls. These two runtime contracts cannot
share the same no-embedding runtime.

The prior formal QA wiring selected `paper_eval.s2_r0_live`. That runtime
deliberately installs a forbidden embedder. Mapping succeeded (49 episodes)
and Episode-only search succeeded, but the formal Edge cosine branch failed at
the first embedding request with:

```text
RuntimeError: S2-R0 forbids embedding requests
```

The first repair then exposed a second stale contract: the formal QA path was
constructing the newer `S2LiveInputs` without its required fields, and that
type also rejects the formal `sfwb-v1-3-*` namespace family. This was an old S2
Judge input projection being reused outside its namespace contract.

## Minimal repair

1. Default formal QA runtime now binds the existing
   `paper_eval.graph_quality_live.build_graph_quality_runtime`, preserving
   pinned embedding identity while keeping construction LLM and cross-encoder
   clients forbidden.
2. QA cleanup uses `GraphQualityRuntime.aclose()` when available, so Graphiti,
   embedding client, and HTTP client are closed exactly once.
3. Mapping and retrieval failures are recorded as separate layers with a
   sanitized error message; an exception is never reported as a measured
   quality score.
4. Formal QA creates a field-equivalent Judge projection with the actual
   formal namespace, question date, and gold session IDs. It does not weaken
   the earlier S2-R0 `S2LiveInputs` namespace guard.
5. Future formal qualification is fail-closed as `FAIL_QA_CONTRACT` whenever
   any history QA decision is invalid. V5 baseline verification rejects such a
   baseline.

## Requalification result

The QA-only run completed in `qa_requalification_20260822-2`:

| History | Rows | Invalid | Correct | Accuracy | Contract |
|---|---:|---:|---:|---:|---|
| `07741c45` | 8 | 0 | 5 | 0.625 | PASS |
| `b6019101` | 8 | 0 | 8 | 1.000 | PASS |
| `6071bd76` | 8 | 0 | 8 | 1.000 | PASS |
| `a2f3aa27` | 8 | 0 | 6 | 0.750 | PASS |
| **Aggregate** | **32** | **0** | **27** | **0.84375** | **PASS** |

Read-only checks: `construction_calls=0`, `graph_write_attempts=0`, and all
`graph_hash_before` values equal their corresponding `graph_hash_after` values.

The earlier `qa_requalification_20260822-1` and the original formal `qa/`
artifacts remain append-only historical diagnostics and are not retroactively
reclassified.
