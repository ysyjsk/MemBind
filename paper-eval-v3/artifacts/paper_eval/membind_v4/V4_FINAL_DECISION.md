# MemBind v4 Final Decision

```text
STATUS: STOP_V4_VDC_NO_LEGAL_WINDOW
METHOD_PHASE: MemBind-VDC (Versioned Dependency Certificates + Partial-Order Bind)
RUN_ID: membind-v31-opt-w4-vdc-capture-20260820-002
HISTORY: 07741c45
SOURCES: 0..11
W: 4
K: 2
DISTANCE: 1
NODE_RESOLVE_ONLY: true
LIVE_CANDIDATE_AUTHORIZED: false
```

The single authorized live action was a capture-only measurement. It used the
configured vLLM services (`qwen3-32b-fp8` on construction port 8000 and
`qwen3-embedding-0.6b` on embedding port 8001), the local Neo4j instance, the
unchanged v3.1 arrival trace, and the frozen W=4/K=2 envelope. It is diagnostic
evidence only and is not eligible for the formal main table.

## Oracle Gate

```text
future PreparedArtifact before predecessor publication: 1 / 11
versioned stale Probe ready before predecessor publication: 0 / 11
CERTIFIED_DISJOINT: 0
CERTIFIED_CONFLICT: 0
UNKNOWN: 11
exact validations: 0
validation HIT: 0
validation MISS: 0
hideable NodeResolve service time: 0 ns
direct correctness violations: 0
published source coverage: 12 / 12
```

The gate therefore returns `STOP_V4_VDC_NO_LEGAL_WINDOW` with reason
`NO_VERSIONED_STALE_PROBE_READY_BEFORE_PUBLICATION`. Source 9 is the only
future artifact that became ready before source 8 publication. Its source 8
predecessor was still in the native commit suffix, and the VDC observation
adapter correctly held the read-only Probe behind the `_committing` state gate.
Launching it earlier would require changing the stateful execution boundary,
which is outside this fixed experiment and would invalidate the gate.

## Capture Integrity

The private bundle was replayed through the installed Graphiti 0.29.3
`node_operations` helpers without contacting Neo4j, the embedding service, or an
LLM provider:

```text
captured binds replayed: 12 / 12
request identity matches: 12 / 12
effect identity matches: 12 / 12
external database calls: 0
external provider calls: 0
```

This confirms that the failed gate is a measured dependency/timing boundary,
not capture corruption. The existing U0, A0, P(C=2), and v3.1 results remain
immutable; no new VDC execution candidate or main-table result is authorized.

**Final disposition: STOP_V4_VDC_NO_LEGAL_WINDOW.**
