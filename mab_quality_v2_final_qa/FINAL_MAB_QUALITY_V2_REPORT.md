# MAB Quality v2 Final QA Report

**Date:** 2026-08-19  
**Lane:** isolated `mab_quality_v2_final_qa`  
**Workplan:** `MemBind_POST_V31_MAB_MULTIQA_AUTORESEARCH_TDD_WORKPLAN_v1.0.md`

## Decision

The QA analysis implementation is complete and the offline/TDD gates are
green. No final live quality number is reported. The live gate is **FAIL
CLOSED** because the frozen construction and embedding vLLM endpoints were
unavailable at the time of the final preflight. The required first probe of
`127.0.0.1:8002` and `127.0.0.1:8003` returned real TCP connection refused,
and the project endpoints at `10.87.5.247:8000/8001` subsequently timed out,
returned proxy 502, and had closed direct TCP ports. This is endpoint/service
unavailability, not a sandbox permission denial.

The complete machine-readable preflight is in
`evidence/FROZEN_ENDPOINT_PREFLIGHT_20260819.json`.

## Dataset Scope

The pinned `MemoryAgentBench` `Accurate_Retrieval` subset contains five
`longmemeval_s*` contexts and 300 QA items. Four contexts map exactly to the
public session inventory (240 QA). The fifth context has one unrecoverable
gold-session mapping defect at question index 38 (`0ddfec37_abs`); its whole
60-QA context was excluded before any quality result, as required by the
workplan. The frozen four-context inventory is recorded in
`evidence/DECLARED_4_CONTEXT_INVENTORY_20260819.json`.

No answer text, `has_answer`, fuzzy matching, fabricated chronology, or
post-hoc QA deletion was used to repair the defect.

## Implemented QA Analysis

- Public/private contracts with recursive gold-blind checks and context hashes.
- Official MAB/LongMemEval session and QA adapter with exact provenance.
- One construction per `(method, context_id)` followed by append-only,
  read-only QA rows.
- Construction receipts, sealed namespace validation, per-QA retry, and
  payload-hash checked resume without reconstruction.
- Quality v1 ContextPack/retrieval/Reader reuse through a compatibility boundary.
- Reader transport with finish-reason validation and zero hidden SDK retries.
- Official Judge wrapper with isolated `*_abs` abstention routing; invalid Judge
  output remains excluded from accuracy.
- Exact U0/MemBind pairing, Recall@1/3/5/10, MRR, nDCG@10, failure
  decomposition, question-type breakdown, and context-cluster bootstrap.
- Bounded AutoResearch ledger (maximum three candidates, no merge authority).
- tmux launcher and artifact-root isolation.

U0 uses the unsalted baseline construction path. MemBind v3.1 uses the frozen
`CACHE_AFFINE` admission policy and a fresh per-run cache salt. Publication
checks query the specific source episode name, not merely namespace existence.

## TDD And Regression Evidence

The isolated suite passed **25 tests**. The existing Quality v1 compatibility
regression passed **21 tests** in the project virtual environment. `compileall`
passed. The workspace does not contain `ruff`, so no ruff result is claimed;
the initial compatibility environment also lacked `rank_bm25`, and the suite
was rerun in the project environment where that dependency is installed.
Details are in `evidence/TDD_REGRESSION_20260819.json`.

## Live Attempts

Three fresh artifact roots were used; none is treated as a result:

| Run | Outcome | Writes |
|---|---|---|
| `mabqv2-smoke-20260819-001` | local Judge-wrapper contract failure, fixed before retry | inventory only |
| `mabqv2-smoke-20260819-002` | vLLM connection error during construction | no receipt, no QA rows |
| `mabqv2-4hist-20260819-001` | final endpoint gate failure | no construction, no QA rows |

The final 4-history command was launched detached in tmux and observed. The
session exited immediately after the frozen endpoint gate, which is the
expected fail-closed behavior when the required models are unavailable. Its
failure manifest is at
`artifacts/live-4hist-20260819-001/FAILURE.json`; the two smoke failure
manifests are retained similarly.

Neo4j was already running and passed a read-only HTTP/Bolt preflight; no
database updates or cleanup were performed. See
`evidence/NEO4J_READONLY_PREFLIGHT_20260819.json`.

## Re-entry Gate

After both frozen vLLM endpoints answer `/v1/models` with the expected model
identities, rerun with a new run id and fresh artifact root. The workflow will
re-run the endpoint gate, use the already frozen four-context inventory, run
the six-QA smoke first, and only then attempt the full four-context,
four-history comparison. Existing namespaces and artifacts must not be reused.
