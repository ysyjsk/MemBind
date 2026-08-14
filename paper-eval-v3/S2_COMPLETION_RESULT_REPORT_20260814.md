# S2 Completion Result Report

Date: 2026-08-14

Run ID: `s2-completion-20260814-001`

Status: `REVIEW_REQUIRED`, complete and internally verified, but not mergeable
as a full S2 PASS. S3 remains unauthorized.

## Scope

This was the single bounded S2 completion authorized by
`S2_COMPLETION_EXECUTION_WORKPLAN_v1.0.md`. It reused the completed 49-session
Native U0 namespace for DEVELOPMENT_EXPOSED history `07741c45` and executed:

```text
one read-only Graphiti Episode BM25/RRF search
-> official LongMemEval flat-session Reader
-> qualified LongMemEval knowledge-update Judge
```

It performed no construction, embedding, cross-encoder, database mutation,
cleanup, or automatic retry. The retrieval policy was selected from benchmark
result-unit and Graphiti API semantics. Because S2-R0 had already been viewed,
the freeze truthfully records `selection_not_blinded=true`, while also binding
`r0_numeric_score_used_for_policy_choice=false` and no candidate score search.

## Result

| Surface | Result |
|---|---:|
| Retrieved sessions | 10 |
| Gold sessions | 2 |
| Covered gold sessions | 2 |
| Gold retrieval ranks | 2, 1 |
| Session Recall_any@10 | 1.0 |
| Session Recall_all@10 | 1.0 |
| Evidence Recall@10 | 1.0 |
| Non-official gold coverage fraction | 1.0 |
| QA Accuracy | 0.0 |
| Judge parse | valid `NO` |

The retriever therefore passed the formal benchmark-unit sanity surface, but
the end-to-end Reader/Judge surface did not. The result correctly remained:

```text
reference_sanity_status = REVIEW_REQUIRED
result_mergeable        = false
s3_ready                = false
s3_authorized           = false
```

## Serving Evidence

The Reader request completed successfully within the frozen 65,536-token
serving envelope:

```text
prompt tokens       27,814
completion tokens   13
prompt characters   121,219
output characters   64
truncation count     0
Reader retries       0
Judge retries        0
```

There was no HTTP/context/KV/RoPE/OOM error, malformed Judge output, or service
failure. The QA zero is therefore not an infrastructure or parser failure.

## Offline Root-Cause Analysis

No additional model request was made. The sealed hashes and frozen dataset were
analyzed offline.

1. The formal retrieved-session-list SHA256 is
   `a464ccb019e5202c4c7c0093021ade405caeffe97a20a0c56b4fd5d1f94c8824`.
   Reconstructing the successful S2-R0 ranked list produced the same hash, so
   the formal run repeated the already-observed deterministic ranking.
2. The prior-state gold session was retrieval rank 2 and chronological Reader
   presentation position 1.
3. The updated/current-state gold session was retrieval rank 1 and Reader
   presentation position 8.
4. The Reader output SHA256 is
   `cf41cd1bc40acfdf8fda62eb3dba40b459ae6ab790c5bb26d7fb947b3e97e9c6`
   with 64 characters.
5. That hash exactly matches a known stale-prior-state answer candidate derived
   from the frozen corpus. The raw candidate is deliberately not persisted.
6. The current reference answer SHA256 is
   `c971fd0518f2801b749ca4123bbeff17b4380a3cc5eddb122eb1a0d0b67da034`
   and does not match the Reader output.

The bounded classification is therefore:

```text
retrieval failure                    false
gold-session availability failure    false
Reader/Judge transport failure       false
Judge parser failure                 false
Reader selected stale prior state    true
whole-Graphiti quality conclusion     NOT_INFERRED
```

The most plausible mechanism is the official chronological presentation of a
long ten-session context: the prior state is presented early and the later
update appears at position 8, after retrieval rank has been discarded for
presentation. This is an observation on one development item, not a causal or
general claim. It does not justify post-hoc prompt, top-k, ordering, model, or
retrieval tuning on this exposed result.

## Scientific Interpretation

This run changes the current diagnosis materially:

- The historical near-zero edge result was a retrieval-surface/metric mismatch.
- The frozen Episode session surface reaches every gold session in the top 2.
- For this knowledge-update item, retrieval availability does not translate to
  QA correctness because the Reader returns the old state despite the updated
  session ranking first.
- Episode retrieval quality, Reader temporal-update reasoning, Judge validity,
  and graph-sensitive construction correctness remain distinct surfaces.

The admissible claim is narrow:

> On one frozen DEVELOPMENT_EXPOSED LongMemEval knowledge-update history,
> Graphiti 0.29.3 Episode BM25/RRF retrieved both gold sessions at ranks 1 and
> 2, but the pinned Qwen3 flat-session Reader selected the stale prior state,
> yielding QA Accuracy 0 under a valid qualified Judge response.

This does not establish general Native Graphiti QA, a failure rate, comparative
MemBind quality, or a final paper retrieval/Reader policy.

## TDD Evidence

The live path was implemented through explicit RED -> GREEN stages for:

```text
S2 completion contract
Episode-to-session mapping and official metrics
official flat-session Reader
synthetic retrieval -> Reader -> Judge chain
safe adapter identity
one-shot policy/qualification/authorization/consumption
durable controller checkpoints
gold-blind formal retrieval
production wiring
sealed result verifier
```

Final post-live regression:

```text
focused S2 result/live surface   59 passed
full offline repository          377 passed
```

Evidence:

- `logs/TDD_RED_S2_COMPLETION_RESULT_VERIFIER_20260814.xml`
- `logs/TDD_GREEN_S2_COMPLETION_RESULT_VERIFIER_20260814.xml`
- `logs/TDD_FOCUSED_GREEN_S2_COMPLETION_POSTLIVE_20260814.xml`
- `logs/TDD_FULL_OFFLINE_GREEN_S2_COMPLETION_POSTLIVE_20260814.xml`

## Artifact Index

| Artifact | SHA256 |
|---|---|
| Contract | `a17b492922c68867ccbaa158dd204a0208ce2eaff0f9e57dcc185b8bf60a5d5b` |
| Adapter identity | `ca587d057c71631b8faf13f697d7141c31b365f4b5c4a898549b82a6d7ffb335` |
| Policy freeze | `45aa71657a23615f540b0426252e457c38d2c4565542d45f8350953c41084473` |
| Offline qualification | `b3cb4eb9f612ac61c5aaf5b0859c8d2630da1e8cebda2f2c727c4304d0414b16` |
| Authorization | `55f2aac2a5b1fbb5d7255a9fe150ea40f2ce94ea9363792b613bcca8f09aed45` |
| Authorization consumption | `7d9dbf9543fe505d9218064ebd9a828e2465d8fd835a40d2cd2c375b5e1ab1d2` |
| Events | `52214a0a0cee493244db240df121233b1656e0ac8398ed5f0c2b756057925041` |
| Checkpoint | `1fcdea3e5a35fb6f8cedc1b97cf334e110046e1b91bfca29fc276ce3dae69032` |
| Result | `d9fc42a6479e3071fce56b8670a583aaa9ad76ce24687f4b6de957173064733d` |
| Full post-live regression | `9b2d6229eef0914d75f606a03876377530648e463d9d7cfb1d51a18659de8a5d` |

Run-local files are under:

`paper-eval-v3/artifacts/paper_eval/native/runs/s2-completion-20260814-001/`

The live console is:

`paper-eval-v3/logs/S2_COMPLETION_LIVE_20260814_001.log`

## Stop Boundary

The one-shot authority is consumed and cannot be reused. No failure artifact
exists, but the result is `REVIEW_REQUIRED` and non-mergeable as a full S2
PASS. The current plan forbids automatic rerun or policy tuning. A future step
must explicitly decide whether to:

```text
retain this as a bounded Reader-quality limitation and keep S3 blocked
or
predeclare a separate Reader characterization/repair plan on disjoint
DEVELOPMENT_EXPOSED evidence before any new numeric execution
```

Neither branch is authorized by this report.
