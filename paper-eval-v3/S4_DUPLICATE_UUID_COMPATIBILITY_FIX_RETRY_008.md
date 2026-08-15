# S4 Duplicate-UUID Compatibility Fix and Retry-008 Evidence

Date: 2026-08-15

Scope: Native Graphiti S4 deterministic-control adapter only.

## 1. Retry-007 terminal state

Retry-007 remains permanently incomplete and non-mergeable. U0 capture
published source sequences `0..11` and stopped before publication of source
sequence 12. D0 replay never started, and no smoke PASS result exists.

```text
phase                       U0_CAPTURE
failure stage               add_episode / source_sequence=12
error class                 CandidateSidecarRuntimeError
exact error                 resolution entity UUID is duplicated
completed episodes          12/49
mergeable                   false
replay started              false
```

Public evidence:

| Artifact | SHA256 |
| --- | --- |
| retry-007 checkpoint | `85dfc77d5c64c56de523c2e31aca0046d08d27b0a9fa810bc1aed7ba223bb12c` |
| retry-007 events | `7ac45c957825ac693a5607e89b0281b9b30e7d69cbb9d381d3d76f3a415593be` |
| retry-007 phase result | `23dcda44bbd6b72471aa1e27f534007908ac4c3af55f4cf3866cd7db3d70afdf` |
| retry-007 execution log | `c48d7539d639c920f8dba80ad2603d4b7078e7ecd4d9fd4d80059c8729df9de7` |

The predecessor verifier rechecks the finalized envelope, hashes, contiguous
prefix, exact failure trace, non-mergeable status, and absence of replay and
smoke-result evidence before retry-008 can be sealed.

## 2. Graphiti shape contract

Pinned Graphiti 0.29.3 retains one resolution result per extracted-node index.
Two extracted entities may resolve to the same canonical `EntityNode`, so the
resolved-node list may legitimately contain the same runtime UUID more than
once. Runtime UUID uniqueness is therefore not a Graphiti output invariant.

The candidate sidecar now applies this selective rule:

```text
same UUID + same canonical endpoint projection + same namespace
  -> coalesce deterministically

same UUID + different canonical endpoint projection or namespace
  -> fail closed
```

Canonical equality reuses the endpoint projection already used by the frozen
logical edge identity: NFKC/trimmed name and summary, sorted unique labels, and
canonical nonvolatile semantic attributes. Candidate rank, position, UUID,
Neo4j ID, group ID, and timestamps are not added to candidate identity. The
namespace is retained only as an isolation consistency check.

This changes no Graphiti behavior, D0 algorithm, workload, prompt, model,
embedding, candidate order, or candidate identity definition. It is an adapter
correctness and compatibility repair.

## 3. Fail-closed accounting retained

The repair does not relax bilateral sidecar gates. The evaluator additionally
requires:

```text
capture unique append count == sealed sidecar record count
replay binding count        == consumed sidecar record count
capture unexpected oracle / fallback / cross-encoder counters == 0
```

Capture resume reuse may be positive, but it cannot replace the one unique
append for each sealed record.

## 4. TDD evidence

RED evidence:

| Gate | Result | SHA256 |
| --- | ---: | --- |
| duplicate UUID compatibility | 6 failed | `0ac8ad0e65523b742a5590028a95a5b86dfb1a0678e1ea2f0d7a9d6ba556e611` |
| bilateral accounting | 6 failed, 1 passed | `2cdd9b81ef5a38d1732b39ed7c4eb40c023dbbbc60c7e091c9e5e8c86eb5ca4a` |
| retry-007 predecessor verifier | collection error | `383aac1c0407a3eaed30995cc6dee3d4b0317b97aeb586844b3dc0fa3146c602` |
| retry-008 tmux launcher | 1 failed | `11518729fca6deacb70a650d6d5dca87bea6df51fbbbc78a4258289c340021ce` |

Final GREEN evidence:

| Gate | Result | SHA256 |
| --- | ---: | --- |
| S4 focused | 378 passed | `a318eaaf689785b209936b97c5031e34a39df8536e59e70f6676ae17b4da5f2f` |
| complete paper-eval-v3 offline | 855 passed | `7d1067ac0898e2c5a59f6419a491d8dd15fd2e2ae6bc55fcae0746f40a17dc7c` |
| real Graphiti EntityNode probe | PASS | console-only bounded probe |
| compileall | PASS | no output |
| git diff --check | PASS | no output |

Bound implementation hashes:

| Source | SHA256 |
| --- | --- |
| `s4_candidate_projection.py` | `d9328cedbc8473f23625fdfb295199a6f809f6586fdd17a4f33f87f09bfd164a` |
| `s4_sidecar_result.py` | `ad189a14339e954178ed0cfa7c4ed38a4b8c5dbd7178f291e24b69403231307b` |
| `s4_retry_008_compatibility.py` | `31058e1f527543b0c9907bdc81b6f3859772af5b2a02f218f04ebf901b29eacf` |

## 5. Retry boundary

This evidence authorizes only creation of a fresh retry-008 contract, its
read-only preflight, and a distinct single-use capture-to-replay authority.
Retry-007 caches, namespace, checkpoint, and consumed authority are never
resumed or reused. Fixed-four qualification, S5, and PILOT remain unauthorized
until retry-008 produces a strictly verified PASS.
