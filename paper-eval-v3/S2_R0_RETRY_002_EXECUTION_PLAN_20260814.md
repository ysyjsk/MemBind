# S2-R0 Replacement Attempt 002 Execution Plan

Status: user-approved replacement execution plan, 2026-08-14.

Target run ID: `s2r0-20260814-002`.

## Purpose

Attempt `s2r0-20260814-001` is terminal and non-mergeable. It failed because
the read-only harness used a Python parameter name that collided with pinned
Graphiti's named Lucene query parameter. The exact production call shape is
now covered by a RED test and the minimal repair is full-offline GREEN.

Attempt 002 is authorized only to repeat the same frozen, read-only Episode
BM25/RRF diagnostic after binding that repair. It is not a retrieval-policy
search, configuration change, corpus rebuild, cleanup, or S3 transition.

## Immutable lineage

```text
prior run                  s2r0-20260814-001
prior authorization        0a83291a4455013a5476e17ba3e9443eb9761ca55acd05b8fbd6a502f2be023a
prior consumption          564e2ee43d7810280d40edefa3a9050e9b1025af974161e94482a07c182acb7d
prior failure              f5709742e6f2209ebfa72d6b8d7b7566af7649774b34adc76819740cd40f71ff
failure classification     HARNESS_QUERY_PARAMETER_NAME_COLLISION
```

The prior authorization is never reused. The prior namespace is read only and
is not cleaned or reconstructed because the failure performed no mutation.

## Frozen scientific contract

Attempt 002 retains the exact attempt-001 contract:

```text
history                    07741c45
namespace                  pev3-s1-20260814-001
expected episodes          49
retrieval                  Graphiti.search_
surface                    EpisodicNode
candidate method           BM25
reranker                   reciprocal rank fusion
top k                      10 sessions
Reader/Judge               absent
construction/model calls   forbidden
database writes            forbidden
cleanup/retry              forbidden
```

No retrieval field, model identity, corpus identity, question, gold sessions,
or metric definition may change between attempts 001 and 002.

## TDD and execution order

```text
retry lineage/path RED tests
-> minimum authorization/controller/script implementation
-> targeted GREEN
-> S2-R0 focused GREEN
-> full offline GREEN
-> seal retry-002 offline qualification
-> seal retry-002 one-shot authorization
-> exclusive consumption before live I/O
-> exact corpus guard
-> exactly one Graphiti.search_ call
-> seal result or sanitized failure
-> STOP
```

The new qualification must bind the prior attempt's authorization,
consumption, failure, root-cause report, repair RED/GREEN evidence, this plan,
the repaired source/tests, and the new final focused/full JUnit files.

## Result boundary

Every successful diagnostic branch remains:

```text
retrieval_policy_selected = false
s3_authorized = false
whole_graph_quality_conclusion = NOT_INFERRED
```

Any attempt-002 failure is sealed and stops without automatic retry. A success
also stops before any Reader/Judge call, policy selection, or S3 transition.
