# V1 Retained-Artifact Embedding Closure

Generated: 2026-08-07

## Decision

V1 passes with explicit evidence limits. The retained evidence is sufficient to
reject live embedding output as a bitwise correctness oracle. No live model,
embedding, Neo4j, or network call was made to reach this decision, and no raw
vector recapture is required.

This is not a numerical root-cause result. The retained artifacts do not support
a claim that embedding variation caused a particular ranking change, prompt
change, or final graph semantic error.

## Controlled Retained Pair

Both source-5 runs completed successfully with the same question, method, mode,
LLM cache, six-episode prefix, zero live LLM calls, and zero post-cleanup nodes.
Each run recorded 42 embedding calls, 95 embedding texts, and 99 cache hits.

- Entity logical records: 18/18 equal.
- Entity embedding hashes: 13 equal, 5 changed.
- Edge logical records: 25/25 equal.
- Edge embedding hashes: 23 equal, 2 changed.
- All retained embedding dimensions: 1024.
- Maximum entity norm absolute delta: 1.0235835656846604e-07.
- Maximum edge norm absolute delta: 9.275672652186984e-08.
- Norm deltas are not component-wise vector deltas.

The two primary forensic evidence hashes are:

```text
source5_run_a_forensics  2f8f8ff951e91604be513b7cdc19b42972123c8e60390f0b0517cc352e7255d3
source5_run_b_forensics  e5377a47f3799bb6b63120ec0ca2a568524abc613d980b81689dd7506125b45d
```

## Query And Downstream Evidence

All 16 retained full-text events pair uniquely. Their input keys, candidate
membership, and candidate order are equal across the source-5 pair.

Each run has 25 cosine events. At aggregate level, the vector-hash bag differs,
the backend candidate-membership bag is equal, and the backend candidate-order,
saved Python order, and selected-membership bags differ. The events do not
retain an exact input hash or stable call correlation ID, and their array order
is completion order. Per-input top-K membership or order change is therefore not
computable.

A separate retained failure at source sequence 5 contains one downstream
candidate substitution and no live LLM fallback. It is association evidence
only. Its causal link to embedding variation is `not_established`.

The following metrics are explicitly recorded as
`not_computable_from_retained_artifacts`:

```text
cross-run cosine
cross-run L2
component-wise maximum absolute difference
changed component count
Neo4j cosine score delta
per-input cosine top-K membership/order change
```

## TDD Evidence

The implementation followed two red-green cycles.

```text
RED   embedding_nondeterminism_retained_red_001.log
      e2500f3e097d28a8fb287790c12818197f3d174f5f8ffe3fc83ade3ee2020492
GREEN embedding_nondeterminism_retained_green_002.log (7 tests, OK)
      5e9f558a9005fc451e15e7c20a3646b5a429aeaec80db6f5a49025ae3f5bfe97
RED   embedding_nondeterminism_cli_red_003.log
      34bb85b91b3dacb9db38319d90982654388b06774c9be5eb7f4473a93d1d08d2
GREEN embedding_nondeterminism_retained_green_004.log (8 tests, OK)
      182f24b4f4e83ab467a5e249aec1b5a3c27b82808103027bb988394443aba5df
```

The second red test failed only because the one-shot CLI did not yet exist. The
green CLI test also proves exclusive output creation and refusal to overwrite an
existing artifact.

## Primary Artifact

```text
path    artifacts/diagnostics/embedding_nondeterminism_source5.json
schema  membind.v1.retained_embedding_closure.v1
sha256  58651ad4a343678934ed88225bafe6ad284bce116680d7dac6e04bfa79691b5c
gate    pass_with_explicit_evidence_limits
next    V2
```

The JSON artifact contains no credential-bearing keys, HTTP headers, raw model
responses, environment dump, or prompt bodies. A second CLI invocation exits
nonzero and leaves the original artifact unchanged.
