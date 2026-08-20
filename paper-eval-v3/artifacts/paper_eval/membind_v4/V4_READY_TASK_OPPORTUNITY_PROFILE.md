# MemBind v4 Ready-Task Opportunity Profile

> Diagnostic-only offline replay; no scheduler, arrival trace, or backend was changed.

## Observable Width

- scheduler peak ready width: `1`
- scheduler peak same-type width: `1`
- workflow peak dependency-ready width: `1`
- workflow peak LLM-heavy ready width: `1`
- ready width >= 2 duration: `0` ns
- P(scheduler ready width >= 2): `0.0`
- P(scheduler same-type width >= 2): `0.0`
- P(workflow ready width >= 2): `0.0`
- P(LLM-heavy ready width >= 2): `0.0`

## Waiting and Critical Path

- ready-task count: `24`
- mean dependency-ready wait: `99792574.70833333` ns
- critical ready residence: `2361568905` ns
- critical/noncritical ready overlap: `0` ns
- critical/noncritical LLM overlap: `2254080` ns
- causality boundary: `OVERLAP_ONLY_NOT_CAUSAL_BLOCKING`

## Observability Boundary

- fine-grained operator profile: `NOT_OBSERVABLE`
- scheduler choice observed: `False`
- same-type choice observed: `False`
- backend speedup proven: `False`
- offline direction: `NO_SCHEDULING_OPPORTUNITY_OBSERVED`

## Terminal Interpretation

The fixed sealed pilot exposes no legal ready width of two or more. This is an offline opportunity result, not a backend speedup claim.
