# APC-Aligned Three-Baseline Development Qualification v1.0

Status: FROZEN FOR EXECUTION

Scope: only `U0`, `A0`, and `P(C=2)` over the four existing
`DEVELOPMENT_EXPOSED` histories (188 episodes per method). Historical runs are
read-only evidence and never enter the new aggregate.

## Frozen workload and timing

- Native service reference: `50.173429214 s`.
- Normalized offered load: `rho=1.2`.
- Inter-arrival: `41.811191012 s`.
- Every method reuses the same per-history relative offset vector and hash.
- Each block has a fresh monotonic origin; absolute wall-clock timestamps are
  deliberately not shared across sequentially executed methods.
- `arrival_ts` is workload time, `service_start` is actual Graphiti entry, and
  `publication_ts` is durable externally visible completion.
- `queue_delay=service_start-arrival`,
  `freshness=publication-arrival`, and backlog is arrived-but-unpublished.

## APC isolation

The deployed vLLM 0.26.0 exposes APC counters and has
`enable_prefix_caching=True`, but `POST /reset_prefix_cache` returns HTTP 404.
The verified equivalent isolation is therefore:

1. hot shared engine;
2. wait for `running=waiting=0` before every block;
3. unique vLLM `cache_salt` per method/history block;
4. identical salt for all calls inside that block;
5. natural within-block APC reuse, with no cross-block prefix identity reuse.

The salt is injected only in the isolated paper-eval runtime wrapper; the
shared legacy `graphiti_native.py` remains unchanged.

## Direct Violations

Every completed block must produce `checker_status=MEASURED`. The checker
combines durable lifecycle evidence, an immediate post-commit visibility
probe, and an independent final Neo4j observation. Frozen categories are:

- lost/missing source;
- duplicate/unexpected source or publication;
- source/publication-order violation;
- publication/visibility violation;
- namespace, endpoint, provenance, and valid/invalid temporal hard violation.

Graphiti's legal many-to-one canonical entity resolution is not a duplicate
source violation. P2 violations are retained as scientific outcomes together
with all performance data.

## Execution order

1. Offline RED tests.
2. Focused GREEN and full offline regression.
3. Read-only model/APC/Neo4j preflight.
4. Full one-history smoke for U0, A0, and P2.
5. If all three checkers are MEASURED, run all 12 balanced blocks in tmux.
6. Seal construction telemetry before any Reader/Judge request.
7. Run frozen Quality Evaluation v1 on the new namespaces without changing
   retrieval, ContextPack, Reader, Judge, or Top-K.
8. Produce the independent JSON/Markdown report and stop.

## Failure policy

Service disconnect, incomplete telemetry, non-fresh namespace, arrival-trace
drift, missing visibility evidence, or an unmeasurable checker causes an
immediate checkpoint and STOP. A measured correctness violation does not stop
or delete the run.
