# S4 Candidate-Index Replay Amendment v1.0

Date: 2026-08-15

Parent workplan: `S4_D0_EXECUTION_WORKPLAN_v1.0.md`

Trigger evidence:
`artifacts/paper_eval/native/runs/s4-d0-replay-20260814-004/DIAGNOSIS_AND_INVALIDATION.json`

## 1. Scope

Retry-004 established that D0 can preserve candidate membership while changing
candidate presentation order and therefore the position IDs embedded in
Graphiti deduplication prompts. Exact prompt hashing correctly stopped, but
returning the captured parsed response without translating those IDs would be
semantically unsafe.

This amendment changes only the read-only D0 replay oracle. U0, Graphiti,
candidate selection, all four D0 stabilizers, embedding replay, construction
configuration, source order, canonical graph comparison, Reader, Judge, and
all later methods remain unchanged.

## 2. Chosen control

D0 keeps exact prompt lookup as its first path. On an exact miss, it may use an
in-memory candidate-index translation only for these pinned Graphiti 0.29.3
prompt surfaces:

```text
dedupe_nodes.nodes
dedupe_edges.resolve_edge
```

For every other prompt, an exact miss remains `UnexpectedPromptError`.

The translation is allowed only when all non-candidate prompt components are
exactly equal, the prompt skeleton outside candidate sections is exactly
equal, each candidate partition has the same prompt-visible membership, and a
unique capture-to-replay identity bijection exists. The cached parsed response
is deep-copied and only its positional references are translated in memory.
The persistent prompt cache, raw response, and token evidence are never
modified.

This is a semantic position translation, not byte-identical output replay.
D0 therefore claims preservation of the captured model decision under a
verified candidate permutation. It does not claim that reordered prompt bytes
would produce the same model output if regenerated.

## 3. Positional fields

Node translation changes only:

```text
entity_resolutions[*].duplicate_candidate_id
```

`-1` remains `-1`. Extracted-entity `id` and `name` are not changed.

Edge translation treats the duplicate and invalidation lists as separate
partitions. It changes:

```text
duplicate_facts[]
contradicted_facts[]
```

`duplicate_facts` must always reference the related-edge partition.
`contradicted_facts` may reference either partition through Graphiti's
continuous index space. Response list order and duplicate references are
preserved because Graphiti uses the first duplicate index as the resolved
edge.

## 4. Fail-closed conditions

Replay stops on any of the following:

```text
candidate member added, removed, or moved across edge partitions
duplicate prompt-visible candidate identity
non-contiguous or non-integer candidate IDs
malformed or repeated candidate tags
non-candidate prompt drift
more than one semantic cache match
cached response index with wrong type, partition, or range
unsupported positional prompt
cache mutation, live fallback, or cross-encoder use
```

The remapper does not hide candidate cutoff drift or non-LLM fast-path drift.
Exact canonical graph parity remains a mandatory final gate.

## 5. Replay evidence

Every replay phase records cumulative integer counters:

```text
exact_prompt_hit_count
candidate_remap_hit_count
candidate_remap_node_hit_count
candidate_remap_edge_hit_count
candidate_remap_rejection_count
```

A PASS requires:

```text
candidate_remap_rejection_count = 0
node_hit_count + edge_hit_count = candidate_remap_hit_count
exact_prompt_hit_count + candidate_remap_hit_count
    = resolved_prompt_count
unexpected prompt/embedding = 0
live LLM/embedding/fallback/cross-encoder = 0
cache hashes unchanged
resolved work counts equal capture
canonical graph parity = 100%
```

Failure events may persist a fixed uppercase machine `error_code`; exception
messages, prompt content, model output, questions, and answers remain private.

## 6. TDD and authority boundary

The implementation must complete:

```text
synthetic RED
 -> node/edge permutation GREEN
 -> fail-closed and property GREEN
 -> production wiring RED/GREEN
 -> full offline GREEN
 -> additive retry contract
 -> read-only preflight
 -> new single-use authority
 -> fresh capture and replay namespaces/cache
```

Retry-004 remains failed and non-mergeable. Its run IDs, namespace, cache, and
authority may not be reused or rewritten. This amendment by itself authorizes
no live action, no four-history qualification, no S5, and no PILOT.
