# S4 Bilateral Sidecar D3 Result Report

Date: 2026-08-15

Scope: offline TDD and production-path qualification only. No vLLM, embedding,
Neo4j, namespace, cleanup, or live retry action was performed.

## Outcome

The D3 mechanism passed the complete offline gate and a fresh retry-006
execution identity was frozen. This does not mean retry-006 has run or passed.

```text
diagnosis prerequisite             SIDECAR_AMENDMENT_JUSTIFIED
focused tests                      147 passed
complete offline regression        801 passed
live service/database calls        0
retry-005 namespace mutation       0
retry-006 live authority           false
next authorized action             read-only preflight only
```

## Qualified mechanism

The production wrapper order is fixed as:

```text
GraphitiPromptCacheLLM
  -> NamespaceNormalizedPromptCache
    -> CandidateSidecarPromptCache
      -> CandidateAwareReplayCache     # replay only
        -> PromptCache
```

Capture writes and fsyncs hash-only candidate-call records. Replay recomputes
the same directed endpoint/provenance/time/attribute projection, requires
partition-preserving membership and unique logical identities, and translates
only `duplicate_facts[]` and `contradicted_facts[]`. Exact visible prompt bytes
still require the hidden logical binding.

Replay uses prepare/acknowledge/commit. Oracle failure, cache miss, ignored
binding, cancellation, membership drift, partition drift, fact drift, foreign
namespace joins, or duplicate logical identities leave the record unconsumed
and fail closed.

Before DB publication, the Graphiti hook requires zero remaining capture calls
for the current source. The runner restores a validated contiguous checkpoint
prefix before installing that hook. Capture resume admits only durable prefix
records plus the current unpublished source; a sealed sidecar requires the
complete checkpoint prefix. Final sealing/verification occurs after canonical
graph export and before cleanup.

## Durable evidence

```text
amendment
S4_BILATERAL_LOGICAL_EDGE_SIDECAR_AMENDMENT_v1.0.md

retry-006 contract
artifacts/paper_eval/native/S4_D0_SIDECAR_RETRY_006_CONTRACT.json
file SHA256
c8c25600d38da62b3560b07ac479f34303cc8337b63205875bb3caef074f7172
contract SHA256
17df1d5f5b312ccee1a5bf303c0dbc65ffec730f3e37f0f2d261ad98b6dd6008

focused JUnit
logs/TDD_FOCUSED_GREEN_S4_SIDECAR_RETRY_006_20260815.xml
SHA256
45238bed22640fdd6e1f81b28b5766a408b15d6bb1803c7841a6d3b191894b22

complete JUnit
logs/TDD_FULL_OFFLINE_GREEN_S4_SIDECAR_RETRY_006_20260815.xml
SHA256
2a4858ce7f24694231bc94d95a3746e1a58c815958a2ebf651044f4fa199104c
```

The contract binds the exact production projection schema SHA256
`28aef596af79a97d73e542e0ed3b21c81fe6dcc7b1fcf442f2f0cddce7436631`,
the diagnosis artifact, amendment, implementation/test source inventory, and
both JUnit gates.

## Current boundary

The only permitted next action is
`scripts/run_s4_sidecar_retry_006_preflight.py`, which performs bounded
read-only model identity/version and Neo4j namespace checks. It has not been
run. A PASS preflight still must be sealed and bound into a distinct
single-use authority before the controller may be started in detached `tmux`.

No fixed-four activation can consume a retry-005 verifier or authority. A
strict sidecar-aware retry-006 PASS result must exist first; activation v2 is
then a separate offline step.
