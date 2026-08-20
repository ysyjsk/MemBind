# MemBind v4 Parallelism Root-Cause Decision

## Scope

This is a sealed offline diagnosis of the existing v3.1 W=4 pilot for
`history=07741c45`, sources `0..11`. It does not change the arrival trace,
workload, scheduler, model, backend, or database. It makes zero network calls
and zero persistent runtime writes. The APC baseline prefix values below are
read-only recomputations and are not new baseline results.

## Funnel Result

| Observable boundary | Peak width | Meaning |
|---|---:|---|
| Source outstanding | 2 | `ARRIVAL` to `PUBLICATION_DURABLE` |
| Coarse workflow ready-waiting | 1 | legal dependency-ready work not dispatched |
| Workflow active | 2 | dispatched stage spans |
| Client LLM request pending | 21 | submitted to terminal |
| Client LLM admission waiting | 20 | submitted to start |
| Client-observed request running | 2 | not GPU execution |

The trace therefore does not show a completely arrival-serial workload, and it
does not show end-to-end dependency serialization. It shows a coarse ready pool
with no scheduling choice plus substantial internal request fan-out/admission
pressure. vLLM batch membership, GPU execution width, and fine-grained operator
identity are not observable in this evidence.

## Terminal Decision

```text
COARSE_READY_POOL_NO_CHOICE_WITH_INTERNAL_LLM_FANOUT
NO_STAGE_SCHEDULER_CHOICE_LLM_ADMISSION_BACKLOG_OBSERVED
```

Do not implement a coarse stage scheduler from this trace. Do not claim a GPU
bottleneck or a speedup from client request overlap. Do not use the 49-source
registered baseline backlog as a 12-source pilot metric; the prefix comparison
is explicitly scope-censored and method/execution identity differs.

The existing v4 NodeResolve lane remains stopped:

```text
STOP_V4_NODE_RESOLVE
```

No c02/c03 or live scheduler candidate is authorized by this diagnosis.
