# MemBind v4 Cross-Layer Parallelism Funnel

> Diagnostic-only replay. No scheduler or live mechanism was added.

## Width Funnel

| Layer | Peak width | Interpretation |
|---|---:|---|
| Source outstanding | 2 | ARRIVAL to publication |
| Workflow ready-waiting | 1 | Legal but not dispatched |
| Workflow active | 2 | Already dispatched |
| LLM request pending | 21 | Submitted to terminal |
| LLM admission waiting | 20 | Submitted to start |
| LLM client running | 2 | Not GPU execution |
| Admission snapshot waiting | 19 | Controller state |
| Admission snapshot active | 2 | Controller state |
| vLLM/GPU internal | NOT_OBSERVABLE | No batch membership trace |

## Decision

- root-cause classification: `COARSE_READY_POOL_NO_CHOICE_WITH_INTERNAL_LLM_FANOUT`
- terminal: `NO_STAGE_SCHEDULER_CHOICE_LLM_ADMISSION_BACKLOG_OBSERVED`
- coarse scheduler authorized: `False`
- backend bottleneck proven: `False`

## Identity and Scope Boundary

The first 12 source hashes, arrival offsets, interarrival, and shared execution envelope are checked against the APC-aligned baseline. The pilot and baseline remain different method/execution identities; the baseline full-run result is not replaced by a prefix result.
