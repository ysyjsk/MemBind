# S4 D0 Smoke Result Report

Date: 2026-08-15

Scope: one DEVELOPMENT_EXPOSED history (`07741c45`) under the frozen S4 D0
smoke contract. This report records a failed correctness qualification. It
does not authorize the four-history qualification, S5, PILOT, or any formal
paper result.

## 1. Result

The retry-004 U0 capture completed, but the D0 read-only replay failed closed
on the third episode because deterministic candidate ordering changed a
position-indexed node-deduplication prompt.

```text
U0 capture       PASS, 49/49 episodes
D0 replay        INCOMPLETE, 2/49 episodes
failure          UnexpectedPromptError at source_sequence=2
S4 smoke         FAIL / STOP
qualification    not authorized
S5               not authorized
PILOT            not authorized
```

The failure was not a model-service or embedding-service disconnect. Replay
made zero live LLM calls, zero live embedding calls, zero fallback calls, and
zero cross-encoder calls before stopping.

## 2. Frozen attempt identity

```text
capture run        s4-d0-capture-20260814-004
capture namespace  pev3-s4-u0-capture-20260814-004
replay run         s4-d0-replay-20260814-004
replay namespace   pev3-s4-d0-replay-20260814-004
cache ID           s4-d0-07741c45-20260814-004
```

The retry contract, preflight, and single-use authority remain immutable:

| Artifact | File SHA256 |
|---|---|
| `artifacts/paper_eval/native/S4_D0_RETRY_004_CONTRACT.json` | `019bf14d932ae9b18f5b3cb01261785db402b697471f0ecf0e74f4d074b2f89f` |
| `artifacts/paper_eval/native/S4_PREFLIGHT_RETRY_004.json` | `c8797e4c1903b888ad66f260a3066f31306c3f070c720be03bf5a88535f5f6d8` |
| `artifacts/paper_eval/native/S4_SMOKE_AUTHORIZATION_RETRY_004.json` | `cca43d98aab89a38e59a5ba4edb93bae03057f8838008b78c3ca2434973878ac` |

## 3. Capture evidence

Capture processed all source sequences `0..48` exactly once and exported a
canonical graph before exact namespace cleanup.

| Surface | Value |
|---|---:|
| Episode coverage | 49/49 (1.0) |
| Logical resolved prompts | 511 |
| Live LLM transport calls | 512 |
| Resolved embeddings | 1,206 |
| Live embedding calls | 66 |
| Unexpected prompts / embeddings | 0 / 0 |
| Live fallback / cross-encoder calls | 0 / 0 |
| Post-cleanup nodes / relationships | 0 / 0 |

The one-call difference between live LLM transport calls and logical prompt
resolutions is a transport retry. Logical work parity uses resolved prompts,
not transport attempts.

Primary capture files:

- `artifacts/paper_eval/native/runs/s4-d0-capture-20260814-004/phase_result.json`
  (`b7b331c4b435b0a5a60d70055cd1c25f022fa35c1e18b86d275924db0946ebfb`)
- `artifacts/paper_eval/native/runs/s4-d0-capture-20260814-004/checkpoint.json`
  (`9b03e8cb883f89d27aa69afc61007c068d40053d33e4eb36003506346caee04d`)
- `artifacts/paper_eval/native/runs/s4-d0-capture-20260814-004/events.jsonl`
  (`26b38e03f2fe0d33eab1b6a59be11d0960dcf3f1e1b3c505dac5bef7b05a9893`)

## 4. Replay failure evidence

Replay completed source sequences `0` and `1`. At source sequence `2`, the
tenth logical prompt was absent from the read-only prompt oracle and replay
stopped immediately.

| Surface | Value |
|---|---:|
| Episode coverage | 2/49 (0.040816) |
| Resolved prompts before stop | 9 |
| Resolved embeddings before stop | 17 |
| Unexpected prompts / embeddings | 1 / 0 |
| Live LLM / embedding calls | 0 / 0 |
| Live fallback / cross-encoder calls | 0 / 0 |
| Prompt cache mutation | none |
| Embedding cache mutation | none |

The prompt cache remained
`d43af27b2f2a914e534b2db684ca4cc4a433671f7ecb79d9120f51f932c044fb`
and the embedding cache remained
`0f0b8b2e1792097b616ea38ef7d67257c5a1e3f47a7c9a4f6dac5016733a5496`.

Primary replay files:

- `artifacts/paper_eval/native/runs/s4-d0-replay-20260814-004/phase_result.json`
  (`d49f301f16cf2e92d2a8856722184633a66b87d7251f447cfd4bbfbb460dd292`)
- `artifacts/paper_eval/native/runs/s4-d0-replay-20260814-004/checkpoint.json`
  (`eaac6d741e132728255230a400193df6748eb1c14ea5c016003e386274385453`)
- `artifacts/paper_eval/native/runs/s4-d0-replay-20260814-004/events.jsonl`
  (`02a0289c10316c34ab7b67c65811c528e09eea5c2f19a7b90c58bce3261042c4`)

## 5. Root-cause diagnosis

The miss occurred in `dedupe_nodes.nodes`. Independent no-raw-content
reconstruction established:

```text
capture prompt hash
320d42919eb6bbb1564d41d58163b940fa57b0b122fb2fa166d71029e4bca7f6

replay prompt hash
ea52679ceffdf0aa7a07ea8fa513fa22391bc7f47f9b0c2a6d7313d142cebb2a

candidate count             2
candidate membership        unchanged
candidate presentation      reordered
candidate IDs               reassigned by position
classification
ORDER_ONLY_CANDIDATE_RENUMBERING_CONFIRMED
```

Sorting the two capture candidates with the pinned D0 node stabilizer
reproduced the replay prompt hash exactly. The cached response for this one
prompt contained 11 entity resolutions and no nonnegative
`duplicate_candidate_id`; this makes the observed item benign, but it does not
make order-insensitive cache reuse generally sound.

Graphiti interprets `duplicate_candidate_id`, `duplicate_facts`, and
`contradicted_facts` as indices into the presented candidate lists. Returning
a cached response after changing candidate order can therefore select a
different entity or edge. A valid repair must either translate every such
index after proving candidate-set identity, or make the runtime candidate
lists follow the captured order before applying the captured response. A
plain order-insensitive prompt key is forbidden.

## 6. Invalidation and cleanup

The failed replay is permanently non-mergeable. Its exact namespace was
cleaned once by its unique `group_id`:

```text
pre-cleanup   4 nodes, 3 relationships
post-cleanup  0 nodes, 0 relationships
global clean  false
```

The sealed evidence is:

`artifacts/paper_eval/native/runs/s4-d0-replay-20260814-004/DIAGNOSIS_AND_INVALIDATION.json`

File SHA256:
`2c3020094f453ce14c9d887f1af696dc84501a727fc47c2baa9ddca2eeff030a`.

The one-shot diagnosis script must not be rerun: its exclusive output now
exists, and its cleanup occurs before the exclusive-write check. The sealed
artifact is the authoritative cleanup record.

## 7. TDD status and next boundary

Focused S4 regression after diagnosis:

```text
34 passed
```

Full paper-eval-v3 offline regression:

```text
551 passed
git diff --check passed
```

JUnit evidence:

`logs/TDD_FULL_OFFLINE_GREEN_S4_POST_DIAGNOSIS_20260815.xml`
(`5dec0aa5f7fa8c233c64019ab86148ad177730079376b6eafc8d1f173712aaeb`).

The next permitted action is offline-only design and TDD for a sound
candidate-index translation or capture-order mechanism. It must cover both
node and edge positional fields, fail closed on ambiguous identities or
membership drift, and pass full offline regression before any new preflight,
authority, cache, namespace, or live replay attempt is created.
