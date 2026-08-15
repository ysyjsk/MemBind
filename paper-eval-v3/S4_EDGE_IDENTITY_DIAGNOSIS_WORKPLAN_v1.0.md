# S4 Edge Identity Diagnosis Workplan v1.0

Date: 2026-08-15

Parent plan: `EXECUTION_PLAN.md`

Evidence anchor:
`S4_D0_REMAP_RETRY_005_FAILURE_REPORT_20260815.md`

## 1. Purpose

Keep the Native baseline and every common-evaluation choice frozen. The only
current question is whether Graphiti edges that collapse to the same
prompt-visible `fact` can be distinguished by a stable, UUID-independent
logical identity at the pre-prompt candidate boundary.

This is a bounded S4 diagnosis. It does not redesign the benchmark, retrieval,
Reader, Judge, K, construction model, Graphiti candidate selection, or
candidate presentation. It grants no cleanup, retry-006, fixed-four
qualification, S5, or PILOT authority.

The Native-baseline qualification critical path therefore remains S4 D0
correctness qualification. D3 closes only the current replay-oracle blocker;
it is neither additional Native tuning nor permission to begin MemBind method
evaluation early.

## 2. Accepted observations

Retry-005 established:

```text
U0 capture                         49/49 PASS
D0 replay completed prefix        source_sequence 0..6
failure source_sequence           7
failure code                      AMBIGUOUS_CANDIDATE_IDENTITY
candidate remap hits              24 (node 6, edge 18)
replay live/fallback/cross calls  0/0/0
cache mutation                    0
```

Private capture-cache analysis may be used locally, but public evidence may
contain only hashes, counts, positions, classifications, and file identities.
For source sequence 7 it establishes that nine edge-deduplication prompts each
contained the same invalidation fact twice. The fact-only projection is
therefore non-injective.

## 3. Important evidence limit

Retry-005 did not record internal candidate metadata at capture time. The
capture namespace was exact-cleaned after PASS, and its terminal canonical
graph has no prompt-hash or candidate-position linkage. The stopped replay
persisted only sanitized failure evidence and a prefix namespace.

Consequently, retry-005 cannot retroactively prove:

```text
capture candidate position -> internal logical edge
replay candidate position  -> internal logical edge
exact capture/replay enriched-identity bijection
```

The diagnosis may prove that a stronger logical identity is available and
unique on the preserved replay prefix. It may not make retry-005 mergeable or
claim that a future sidecar already works.

## 4. Frozen prohibitions

- Do not modify the U0 algorithm, Graphiti candidate selection/presentation,
  prompt bytes, or Graphiti result. D3 may add only an observational
  pre-prompt capture hook whose non-interference is proved offline.
- Do not change dataset, split, retrieval, K=10, Reader-v2, Judge, model, or
  embedding identity.
- Do not clean or mutate `pev3-s4-d0-replay-20260815-005`.
- Do not create a capture, replay, qualification, S5, or PILOT namespace.
- Do not call a live LLM, embedding endpoint, fallback, or cross-encoder.
- Do not use candidate position, search rank, Neo4j element ID, runtime UUID,
  group ID, or `created_at` as logical identity.
- Do not persist raw prompts, responses, facts, endpoint text, questions,
  answers, episode bodies, credentials, or UUIDs in public artifacts.

## 5. D1: persisted-evidence diagnosis

Recompute from the sealed retry-005 files:

```text
failure source and error code
capture/replay coverage and cache hashes
source-7 edge-prompt count
invalidation candidate count and duplicate multiplicity
duplicate prompt-visible identity SHA256
terminal capture-graph matching-edge count
which logical component hashes differ
```

The expected bounded classification is:

```text
NON_INJECTIVE_FACT_ONLY_EDGE_CANDIDATE_IDENTITY_CONFIRMED
```

This stage is local and read-only. It does not inspect Neo4j.

## 6. D2: exact replay-prefix dry run

Submit only source sequence 7 to the preserved replay prefix. This is not a
replay resume and may not publish an episode.

Before any Neo4j access, the diagnosis harness must pass synthetic RED,
focused GREEN, and complete offline GREEN tests. A single-use read-only
diagnosis contract must bind the retry-005 input-file hashes, namespace,
history, source sequence, source hash, cache hashes, projection schema, and
the only permitted output path. It grants no cleanup or retry authority.

The production harness must install all of these fences before the call:

1. Prompt and embedding caches remain read-only and retain pre/post SHA256.
2. Underlying live LLM, embedding, and cross-encoder clients are replaced by
   sentinels that raise before network I/O.
3. Every reachable Neo4j query, session, transaction, and graph-operation
   write entry point fails closed. Permitted queries must be read-routed and
   non-mutating.
4. `graph._process_episode_data` is replaced by a hard publication fence.
5. `resolve_extracted_edge` is observed before its prompt is rendered. The
   hook records the expected ten source-7 edge calls using a hash-only new-edge
   call correlation, then issues one dedicated diagnostic stop after all ten
   calls are accounted for. A missing or duplicate correlation is incomplete
   evidence, not a successful diagnosis.
6. Complete namespace snapshots are hashed before and after; counts, episode
   names, and snapshot SHA256 must be identical.
7. The original module functions and clients are restored and the graph
   driver is closed on every exit path.

The hook may inspect runtime UUIDs only as in-memory join keys. Under the same
read-only guard it must resolve every endpoint node and every provenance
episode, and map provenance completely through the frozen 49-episode manifest
to `(source_sequence, source_hash)`. Missing, duplicate, cross-namespace, or
unmapped joins are incomplete evidence. UUIDs may not enter the logical
identity or public artifact.

## 7. Candidate logical identity under test

The diagnosis evaluates the following directed edge projection within each
candidate partition:

```text
exact fact SHA256
relation/name SHA256
source endpoint logical fingerprint SHA256
target endpoint logical fingerprint SHA256
valid_at / invalid_at / reference_time normalized hashes
expired boolean
canonical semantic-attributes SHA256
sorted provenance (source_sequence, source_hash) SHA256
```

An endpoint fingerprint contains exact normalized name, sorted labels,
summary, and canonical nonvolatile attributes. Direction is preserved.
UUIDs, group IDs, embeddings, `created_at`, update timestamps, and raw
`expired_at` wall-clock values are excluded.

`related` versus `invalidation` is a separate structural constraint, not an
identity component. Capture/replay membership must be equal within each
partition. Moving a logical edge across partitions fails closed as partition
drift; adding `partition` to an identity may not conceal that drift.

## 8. Diagnosis verdict

Only two outcomes are allowed:

```text
SIDECAR_AMENDMENT_JUSTIFIED
```

Requires the duplicated fact class to have the expected multiplicity and a
unique enriched logical identity for every observed replay candidate, with
zero read-only or cache fence violation. Because capture position linkage is
unavailable, this verdict authorizes only offline sidecar design and TDD.

```text
LOGICAL_IDENTITY_STILL_AMBIGUOUS_STOP
```

Used if two candidates remain identical after every allowed stable component,
or if any evidence/fence check is incomplete. The sealed reason must
distinguish `IDENTITY_AMBIGUOUS` from `EVIDENCE_INCOMPLETE`. Do not add
position, rank, UUID, or time-of-execution fields to force uniqueness.

## 9. D3: two-sided sidecar amendment

If and only if D2 returns `SIDECAR_AMENDMENT_JUSTIFIED`, implement a new
attempt-scoped mechanism at the same pre-prompt edge boundary:

```text
capture: hash-only internal candidate metadata -> sealed private sidecar
replay:  same projection computed in memory
         -> partition-preserving multiset equality
         -> unique logical-identity bijection
         -> positional response translation
```

A capture-only sidecar is insufficient because the replay prompt wrapper sees
only `{idx, fact}`. Replay must expose the same internal candidate projection
before prompt rendering. Fully identical logical identities still fail closed.

D3 is not complete when the pure sidecar data structures pass unit tests. The
attempt-scoped mechanism must also pass an offline production-integration gate
against the pinned S4 runtime path: the pre-prompt hook, actual normalized
prompt-hash association, task-local concurrent-call isolation, exact-once
capture/replay consumption, crash/resume behavior, final sidecar sealing, and
capture-hook non-interference must all be exercised with synthetic clients and
no model or database service. This gate must include focused tests followed by
the complete `paper-eval-v3` offline regression. Any integration or regression
failure returns D3 to RED and grants no retry authority.

Minimum RED matrix:

1. Same fact, different directed endpoints remaps correctly.
2. Same fact/endpoints, different provenance remaps correctly.
3. Independent related/invalidation permutations preserve decisions.
4. Fully identical logical identities fail closed.
5. Membership or partition drift fails closed.
6. UUID, rank, position, group ID, or `created_at` dependence fails closed.
7. Sidecar or cache mutation, missing call correlation, or collision fails.
8. Resume consumes each source/call record exactly once.
9. Public evidence rejects raw/private fields.
10. Enabling the capture hook leaves prompt bytes/hash, candidate membership
    and order, model response, and Graphiti output byte-identical.

After focused GREEN and complete offline GREEN, seal a separate amendment,
contract, preflight, and single-use authority for a fresh retry-006. Retry-005
remains failed and non-mergeable.

## 10. Unchanged retry-006 PASS gate

```text
capture/replay coverage                 49/49 and 49/49
unexpected prompt/embedding             0/0
replay live LLM/embedding/fallback      0/0/0
cross-encoder                            0
candidate translation rejection         0
prompt/embedding/sidecar mutation       0
sidecar call and partition coverage      exact
sidecar logical identity consumption     exactly once
capture/replay resolved work parity      exact
canonical graph parity                   100%
```

The capture sidecar is sealed after capture; replay must open it read-only and
retain the same SHA256 before and after use.

No percentage relaxation or heuristic fallback is allowed.

## 11. Downstream boundary

Only a strictly verified retry-006 smoke PASS may generate a new,
sidecar-aware result schema/verifier and additive qualification activation v2
for the already sealed four DEVELOPMENT_EXPOSED histories. The existing
activation v1 is retry-005-specific and may not consume retry-006 evidence.
The fixed four are four distinct histories total: the smoke history plus three
fresh qualification blocks. The history set may not change. Fixed-four PASS
freezes D0 and then permits S5 offline design; it does not reopen Native
baseline tuning.

The critical path is fixed and sequential:

```text
D3 bilateral-sidecar offline production integration
  -> focused GREEN
  -> complete paper-eval-v3 offline GREEN
  -> separate amendment / contract / preflight / single-use authority
  -> fresh retry-006 smoke
  -> sidecar-aware activation v2
  -> sealed fixed-four S4 qualification
  -> D0 freeze
  -> S5 offline design
```

No step implicitly authorizes the next one. This workplan itself authorizes no
live retry, namespace creation, fixed-four block, S5 action, or PILOT action.

### Current completion state

D3 production integration passed its final offline gates:

```text
focused bilateral-sidecar suite          147 passed
complete paper-eval-v3 suite              801 passed
git diff --check                           passed
retry-006 contract                         sealed
```

The sealed contract is
`artifacts/paper_eval/native/S4_D0_SIDECAR_RETRY_006_CONTRACT.json` with file
SHA256
`c8c25600d38da62b3560b07ac479f34303cc8337b63205875bb3caef074f7172`.
It authorizes only the bounded read-only preflight. Live execution remains
false, and no namespace/cache/sidecar has been created for attempt 006.

## 12. Sealed diagnosis artifact

The only D1/D2 public output is:

```text
artifacts/paper_eval/native/S4_EDGE_IDENTITY_DIAGNOSIS_RETRY_005.json
```

It contains only input-file SHA256 bindings, projection-schema SHA256, D1
counts and duplicate multiplicity, D2 per-call/per-partition hash-only
identity multiplicities, cache and namespace pre/post hashes, zero
network/write counters, verdict, and fixed reason. It must explicitly deny a
retroactive retry-005 capture/replay bijection and all downstream authority.
It may not contain runtime UUIDs, raw endpoints, facts, prompts, responses,
questions, answers, episode content, or credentials, and it is created once
with exclusive finalization.
