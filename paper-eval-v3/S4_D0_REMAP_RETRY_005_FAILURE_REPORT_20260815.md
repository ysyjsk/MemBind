# S4 D0 Candidate-Remap Retry-005 Failure Report

Date: 2026-08-15

Scope: one DEVELOPMENT_EXPOSED history (`07741c45`) under the sealed S4
candidate-index remap amendment. This report records a correctness-preserving
fail-closed result. It does not authorize the fixed-four qualification, S5,
PILOT, cleanup, or another retry.

## 1. Verdict

```text
U0 capture                 PASS, 49/49 episodes
D0 read-only replay        INCOMPLETE, 7/49 episodes
completed replay prefix    source_sequence 0..6
failure source_sequence    7
error class                CandidateRemapError
error code                 AMBIGUOUS_CANDIDATE_IDENTITY
failure stage              add_episode
retry-005                  FAIL / NON-MERGEABLE / STOP
```

This was not a vLLM, embedding, Neo4j transport, context-envelope, or model
availability failure. Replay made zero live model calls and stopped at the
first semantic condition for which a unique candidate-ID translation could
not be proved.

## 2. Frozen attempt identity

```text
capture run        s4-d0-capture-20260815-005
capture namespace  pev3-s4-u0-capture-20260815-005
replay run         s4-d0-replay-20260815-005
replay namespace   pev3-s4-d0-replay-20260815-005
cache ID           s4-d0-remap-07741c45-20260815-005
```

| Sealed artifact | File SHA256 |
|---|---|
| `artifacts/paper_eval/native/S4_D0_REMAP_RETRY_005_CONTRACT.json` | `bb122deec49e9f4f015639b46be49a1cbd2c6cca0afa49574033f0c4c3841922` |
| `artifacts/paper_eval/native/S4_PREFLIGHT_REMAP_RETRY_005.json` | `371560169bbcff21bed7864a7be8206d5c1f51243983764e5555fc7c237f0001` |
| `artifacts/paper_eval/native/S4_REMAP_SMOKE_AUTHORIZATION_RETRY_005.json` | `1dc0f8970251496c78b22166b449e3d72f85e6e10efc84022925e8c87c7c3d87` |
| `artifacts/paper_eval/native/runs/s4-remap-smoke-retry-005/S4_REMAP_AUTHORITY_CONSUMPTION.json` | `8d0d50791475069158696b9ff909ddeccbbdba15a5b033902cbeffa6873ec9e0` |

## 3. Capture evidence

Capture processed source sequences `0..48` exactly once, exported the full
canonical graph, sealed the private caches, and exact-cleaned only its own
namespace.

| Surface | Value |
|---|---:|
| Episode coverage | 49/49 (1.0) |
| Logical prompts | 531 |
| Live LLM transport calls | 532 |
| Resolved embeddings | 1,242 |
| Live embedding calls | 67 |
| Unexpected prompts / embeddings | 0 / 0 |
| Fallback / cross-encoder calls | 0 / 0 |
| Post-cleanup nodes / relationships | 0 / 0 |

The one extra LLM transport call is a transport retry; logical work uses the
resolved-prompt count. The capture canonical-graph SHA256 is
`f55f0aa2011c3a48fe9e8002bf7a40ab7ffdb024a42fd2ad9fcd1dc1902bc022`.

Primary capture files:

- `artifacts/paper_eval/native/runs/s4-d0-capture-20260815-005/phase_result.json`
  (`e805dc6bf9715fd6a1118c26d0608f14ea43379f185be687fac09f3d772fdc78`)
- `artifacts/paper_eval/native/runs/s4-d0-capture-20260815-005/checkpoint.json`
  (`7fec75961e7bb4693e1e3b5819b387033f2129562d88f111c5375c2eebaf5805`)
- `artifacts/paper_eval/native/runs/s4-d0-capture-20260815-005/events.jsonl`
  (`f0043e104a3995049b5937c8acf4a4031b8a731811c1b84efa5b34935203f397`)

## 4. Replay failure evidence

Replay successfully exercised both candidate-remap surfaces before stopping:

| Surface | Value |
|---|---:|
| Completed episodes | 7/49 (`0..6`) |
| Resolved prompts before stop | 77 |
| Exact prompt hits | 44 |
| Candidate-remap hits | 24 |
| Node / edge remap hits | 6 / 18 |
| Remap rejection attempts | 9 |
| Resolved embeddings | 175 |
| Live LLM / embedding calls | 0 / 0 |
| Unexpected prompts / embeddings | 0 / 0 |
| Fallback / cross-encoder calls | 0 / 0 |

The prompt cache remained
`0a0cf225623c1ea4a153516806a8895d7bdd565fc46c4d468f6e357a74071cc9`
and the embedding cache remained
`47f2a4ad0897ac5ab9298bf11738144e1d59d41aa84e2019035597726876310b`.
These exactly match the capture-sealed cache hashes.

Primary replay files:

- `artifacts/paper_eval/native/runs/s4-d0-replay-20260815-005/phase_result.json`
  (`1312b9cf72738edcfb242c625d476c486823c5c1a2e943863def87a3a7609995`)
- `artifacts/paper_eval/native/runs/s4-d0-replay-20260815-005/checkpoint.json`
  (`cce0c867d8aac5056107cd8fa387200b327cb237f16e756581651557cc368088`)
- `artifacts/paper_eval/native/runs/s4-d0-replay-20260815-005/events.jsonl`
  (`a3a74dc044821e31d0265d4612b68b4f09c517964d9d7259770e929854d31b15`)

## 5. Correctness interpretation

The exception originated while building the edge invalidation candidate map.
At least one candidate identity was duplicated under the fields visible in
the Graphiti prompt, so capture and replay candidates could not be related by
a unique bijection. Translating positional indices under that ambiguity could
silently invalidate or resolve the wrong edge.

The remapper therefore behaved as designed: exact hits passed through,
unambiguous node and edge permutations were translated, and an ambiguous edge
identity stopped replay instead of guessing. This result shows that the
current prompt-visible identity is insufficient as a general D0 replay key;
it does not show a model-service problem and does not justify weakening the
oracle.

## 6. Preservation and authority state

The failed replay namespace is intentionally preserved for a later explicit,
read-only diagnosis:

```text
namespace       pev3-s4-d0-replay-20260815-005
nodes           32
relationships   48
episodes        7
cleanup         not performed
```

`S4_D0_REMAP_SMOKE_RESULT.json` was not generated. Consequently,
`S4_QUALIFICATION_ACTIVATION_OVERLAY.json` was not generated and the sealed
fixed-four plan remains non-authorizing. No qualification namespace, S5 work,
or PILOT work was started.

## 7. TDD state and next boundary

Before the live attempt, the candidate oracle, controller, authority, resume,
result verifier, cache immutability, and graph-parity gates passed the complete
offline suite. During the stable capture wait, an additive qualification
activation layer was developed without modifying live-bound retry-005 source.
It can only finalize after the strict retry-005 verifier returns PASS.

```text
activation RED                 missing module, expected failure
activation focused GREEN       10 passed
paper-eval-v3 full offline     632 passed
activation artifact           absent (correct after retry-005 failure)
```

JUnit evidence:

`logs/TDD_FULL_OFFLINE_GREEN_S4_QUALIFICATION_ACTIVATION_20260815.xml`
(`43a63e0543c992482aaf862e0437dbe1ec421ee83cb761fff22ca6c663a3ccd4`).

The final post-failure closure rerun is
`logs/TDD_FULL_OFFLINE_GREEN_S4_RETRY_005_FAILURE_CLOSURE_20260815.xml`
(`2812d96089538df7e29e32312c1b72ac849d7b02a596c4df901ddb37d7a9cb90`),
also with 632 tests passing.

The next action requires a new explicit design decision. Permissible offline
options are to enrich the edge candidate identity with independently captured
trace evidence, implement a trace-order oracle with a disclosed contract, or
reconsider whether D0 needs candidate-presentation stabilization. No option is
authorized by this report, and no retry or cleanup should occur automatically.
