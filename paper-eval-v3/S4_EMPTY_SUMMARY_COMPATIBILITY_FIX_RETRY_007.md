# S4 Empty-Summary Compatibility Fix and Retry-007 Evidence

Date: 2026-08-15

## Decision

Retry-006 is permanently `INCOMPLETE` and non-mergeable. It consumed its
single-use authority and stopped during `U0_CAPTURE`, source sequence 0, before
publishing any graph state. Retry-007 is a fresh execution identity; it does
not resume or reuse retry-006's namespace, private cache, authority, or run
artifacts.

## Retry-006 Evidence

```text
failure stage                 add_episode / source_sequence 0
error class                   CandidateSidecarRuntimeError
completed episodes            0 / 49
checkpoint namespace state    0 nodes / 0 relationships
D0 replay                     not started
mergeable                     false
```

Public artifact file SHA256 values:

```text
contract
c8c25600d38da62b3560b07ac479f34303cc8337b63205875bb3caef074f7172

preflight
c70d486daafe692811d300a3e53338b87ca2a987ad9035c4ab175d0cb12ae3ef

authority
24029e2a3d29d583f0dafce5b13e50631a8fe242a0b145e2065af49dc8ee404f

authority consumption
cce899a09acac5ba2ff74002c10cfd3f6215c3f1b4bd23104d8b494fdc713f70

capture checkpoint
490799efc274426ce9197fa97d5740c33856106f6e90966a1bd10ad22a3b018e

capture events
73831b9165443f492e3c3fc7b9d96d22947c8865724b104aad427911b7c08722

capture phase result
2e2ae9d286ffee08006ab5948969f752b7b1c478544947d25ec20249b8af053b
```

## Root Cause

The outer hook selected the correct positional argument: pinned Graphiti
0.29.3 passes `nodes: list[EntityNode]` at position 6 of
`Graphiti._extract_and_resolve_edges`. The mismatch was a lifecycle
assumption, not an argument-index or object-type error.

Graphiti creates a new `EntityNode` with a valid empty `summary` string and
empty `attributes` mapping. It resolves edges before the later attribute and
summary hydration step. The sidecar projection incorrectly required summary
text to be nonempty both while installing the resolution-entity context and
while computing the final logical edge identity.

## Minimal Fix

The endpoint summary policy is now:

```text
empty string                  valid, canonicalized as ""
nonempty string               valid, NFKC-normalized and stripped
missing or non-string value   fail closed
```

Summary remains a semantic endpoint component. No placeholder is invented,
and runtime UUID, candidate position, rank, group ID, Neo4j ID, or creation
time remains forbidden as a semantic identity fallback. Capture and replay
with different runtime UUIDs and the same pre-hydration endpoint state must
produce the same logical identity. Existing duplicate-logical-identity and
bijection checks remain unchanged and fail closed.

## TDD Evidence

The initial regression test reproduced retry-006 exactly: it failed first at
the outer endpoint gate. After the first minimal change, the same test remained
red at the final logical-identity gate. Only after correcting both layers did
it turn green. Additional tests require UUID-independent capture/replay parity,
deterministic empty-summary hashing, rejection of non-string summaries,
attempt-scoped retry-007 identity, edge-identity source binding, and detached
tmux launch safety.

```text
focused gate                  181 passed
focused JUnit SHA256          2300d88901c71805b724516d5b5373beaa83a5228dd5de935c539debd598d552

complete offline regression   815 passed
full JUnit SHA256             8e708a5e5af6aab9df959284b916286e3273b7e98833f2b7a81db24afdcae7e9

compileall                    PASS
git diff --check              PASS
pinned EntityNode probe       PASS
```

The pinned Graphiti probe instantiated real 0.29.3 `EntityNode` objects with
their default empty summaries and confirmed that capture/replay projections
with different physical UUIDs produced one identical logical identity.

## Claim Limits

This document authorizes only creation of a fresh retry-007 contract and its
read-only preflight. It is not a live S4 PASS, does not authorize the fixed-four
qualification, and does not authorize S5 or PILOT. Those gates remain false
unless the strict retry-007 capture/replay evaluator emits a complete PASS.
