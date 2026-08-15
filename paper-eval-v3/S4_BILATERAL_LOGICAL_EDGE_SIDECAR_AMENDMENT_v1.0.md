# S4 Bilateral Logical-Edge Sidecar Amendment v1.0

Date: 2026-08-15

Parent workplan: `S4_EDGE_IDENTITY_DIAGNOSIS_WORKPLAN_v1.0.md`

Trigger evidence:
`artifacts/paper_eval/native/S4_EDGE_IDENTITY_DIAGNOSIS_RETRY_005.json`

## 1. Scope

Retry-005 remains failed, incomplete, non-mergeable, and preserved. Its
capture did not record internal candidate linkage, so no later analysis may
retroactively claim a capture/replay bijection for that attempt.

The sealed diagnosis found that the fact-only edge identity was non-injective,
while the allowed UUID-independent logical edge projection was unique on all
ten observed source-7 pre-prompt calls. This amendment therefore changes only
the attempt-scoped S4 correctness oracle for a fresh capture/replay pair. It
does not change Graphiti, U0, candidate selection or order, prompt bytes,
model output, embedding behavior, dataset, retrieval, Reader, Judge, K, or any
MemBind method.

## 2. Bilateral control

The edge control is bilateral at the same pre-prompt Graphiti boundary:

```text
capture pre-prompt projection
  -> associate the actual namespace-normalized prompt SHA256
  -> append+fsync a hash-only candidate-call record
  -> seal the sidecar after canonical export and before cleanup

replay pre-prompt projection
  -> prepare the matching capture call
  -> prove partition-preserving logical-identity bijection
  -> translate only cached positional response references in memory
  -> commit consumption only after the replay oracle acknowledges the binding
```

A capture-only sidecar is invalid. Edge prompts, including exact prompt-byte
hits, must pass through the bilateral binding. Existing prompt-visible node
candidate translation remains unchanged and does not use the edge sidecar.

The required wrapper order is:

```text
GraphitiPromptCacheLLM
  -> NamespaceNormalizedPromptCache
    -> CandidateSidecarPromptCache
      -> CandidateAwareReplayCache     # replay only
        -> PromptCache
```

## 3. Logical identity

Within each independent `related` or `invalidation` partition, a candidate is
identified by the SHA256 of:

```text
exact fact
relation/name
directed source endpoint semantics
directed target endpoint semantics
valid_at / invalid_at / reference_time
expired boolean
canonical semantic attributes
sorted frozen-source provenance
```

Endpoint semantics contain the exact normalized name, sorted labels, summary,
and canonical nonvolatile attributes. Runtime UUIDs may be used only as
in-memory join keys. Candidate position, rank, Neo4j ID, UUID, group ID,
`created_at`, and execution order are prohibited identity components.

Partition is a structural equality requirement, not an identity component.
Membership drift, partition drift, fact drift, foreign namespace joins, or
duplicate logical identities fail closed.

## 4. Fast paths and publication fence

Graphiti paths that render no edge-deduplication prompt create no artificial
sidecar record. The two asymmetric cases are still rejected:

```text
capture fast path, replay prompt
  -> SIDECAR_CALL_CORRELATION_MISSING

capture prompt, replay fast path
  -> unconsumed source call blocks _process_episode_data before DB publication
```

An edge prompt without an active task-local projection fails before prompt
cache or live-client side effects. Concurrent edge calls use `ContextVar`
scopes; exceptions and cancellation restore both projection and replay
binding contexts.

## 5. Durability and resume

Capture records contain only hashes, integer candidate IDs, source sequence,
and schema/attempt identities. Each append and seal is fsynced. A prompt-cache
write followed by a sidecar append failure cannot publish the episode; an
exact cache hit on a valid resume repairs or verifies the same record.

The runner validates its contiguous checkpoint before restoring replay
consumption. Completed sources are reconstructed as consumed; the current
unpublished source remains retryable. An unsealed capture may contain records
only for the completed prefix and the current failed source. Future-source
records fail closed.

A sealed capture can resume only when the checkpoint covers the complete
history, which handles the seal-to-terminal-checkpoint window. Replay opens
the sealed sidecar read-only, retains its SHA256, and must finish with:

```text
prepared_count = 0
remaining_count = 0
consumed_count = record_count
```

The sidecar seal binds the final prompt-cache SHA256, embedding-cache SHA256,
record-set SHA256, record-key SHA256, projection schema, and per-source call
count digest.

## 6. Retry-006 hard gates

The fresh smoke PASS requires all existing S4 gates plus:

```text
capture/replay coverage                         49/49 and 49/49
sidecar file SHA256                             identical after capture/replay
sidecar record and logical-call coverage        exact
replay prepared / remaining calls               0 / 0
replay consumed calls                           sidecar record count
sidecar candidate rejection                     0
candidate-remap rejection                       0
edge sidecar oracle accounting                  exact
prompt / embedding / sidecar mutation           0
replay live LLM / embedding / fallback / cross  0 / 0 / 0 / 0
canonical graph parity                           exact 100%
```

No percentage relaxation, heuristic fallback, trace-order mapping, or cache
rewrite is permitted.

## 7. TDD and authority boundary

The implementation completed focused and full offline gates before any retry
contract can be finalized. A separate retry-006 contract must bind this
amendment, the sealed diagnosis, projection schema, source inventory, tests,
and JUnit evidence. A later read-only preflight may check only the frozen model
identities, vLLM 0.26.0/65536 envelope, Neo4j connectivity, exact-empty fresh
namespaces, and the unchanged historical S1 anchor.

This amendment alone authorizes no live request, namespace creation or
cleanup, authority consumption, fixed-four qualification, S5, or PILOT.
