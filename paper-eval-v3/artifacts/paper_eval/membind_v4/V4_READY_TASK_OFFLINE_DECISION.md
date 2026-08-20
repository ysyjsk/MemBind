# MemBind v4 Ready-Task Offline Decision

## Scope

This diagnostic replays the existing sealed v3.1 W=4 pilot for
`history=07741c45`, sources `0..11`, without changing the arrival trace,
workload, scheduler, model, backend, or database. It performs no network calls
and no persistent writes. The result is not formal main-table evidence.

## Result

The scheduler aggregate and dependency-reconstructed workflow view both show
`peak ready width = 1` and zero time at ready width `>= 2`. The observable
request-kind groups are only coarse `COMPILE` and `BIND/FRONTIER`; the sealed
trace has no member IDs or fine Graphiti operator labels, so
EntityExtract/EdgeExtract/NodeResolve same-type width is `UNAVAILABLE`, not
zero.

Ready wait is measured from dependency readiness to actual dispatch. Overlap
between a frontier-critical ready interval and noncritical LLM service is
reported only as an overlap proxy; it is not treated as causal blocking.

## Decision

```text
NO_SCHEDULING_OPPORTUNITY_OBSERVED
```

Do not implement or live-test a new scheduler on this trace. A future
controlled scheduler study would require a trace that actually exposes at
least two legal ready tasks, or a separately authorized workload amendment;
neither is authorized by this offline study. Do not infer vLLM batching or
throughput gains from aggregate ready width alone.

The existing conflict-aware NodeResolve terminal remains independent:

```text
STOP_V4_NODE_RESOLVE
```
