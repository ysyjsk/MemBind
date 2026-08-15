# S3 Native Baseline v2 Configuration Freeze Result

Date: 2026-08-14

Run ID: `native-baseline-v2-freeze-20260814-001`

Final status: `PASS` for configuration freezing only. Native quality remains
`NOT_ESTIMATED`; S4 live execution and PILOT are not authorized.

## 1. Decision

The proposal's narrow execution direction is accepted:

```text
LongMemEval-S
 -> Graphiti 0.29.3 native construction
 -> Graphiti Episode BM25/RRF retrieval, K=10
 -> one-to-one Episode/session adapter
 -> pinned LongMemEval JSON + both sides + single-call CoN Reader
 -> Qwen3-32B-FP8
 -> qualified LongMemEval Judge
```

No additional 8-16 item qualification wave, K sweep, retrieval redesign,
benchmark, baseline, or model change is scheduled. Aggregate quality belongs
in the later disjoint PILOT, where U0/A0/P*/M* use the same Reader and Judge.

Two methodological corrections remain explicit. The old direct Reader was a
supported LongMemEval path rather than an invalid ad-hoc prompt, and Reader-v2
was selected non-blind after that path failed on an exposed development item.
The Reader-v2 canary is compatibility evidence only; its QA/Recall values are
not copied into the S3 freeze or used in its common method-policy hash.

## 2. Frozen identity

All four methods bind to one common policy:

```text
U0 == A0 == P* == M*
method policy SHA256
5699b88d83ad71de1119930ece69a9176c352ed847ea02be0cacc661b46e79e8
```

Key components:

| Component | Frozen identity |
|---|---|
| Dataset | `d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442` |
| Retrieval | Graphiti Episode BM25/RRF, `Graphiti.search_`, K=10 unique sessions |
| Retrieval config | `411df587095daf9284ffaa8399a66886e88329999d934a26e28e0d43caad7d46` |
| Reader-v2 config | `35cda64f27664f1901b2bf129cc95b5d77e8c51cac90abfbbe1c4118dd92737b` |
| Judge component | `bfdef9ccfc25938153473056962e4f91d3a7924e56b6a6f7672dcbdc6877acdd` |
| Judge transport | `97fc7c64f9ce991383e68269054254dcd36790dc493d9795418f58b763d27d6d` |
| Graphiti | v0.29.3, commit `021d3a57d511f21b10adaf7fa923bd5c1fce5e9d` |
| S1 construction | 49/49 episodes, serial source order, zero failure/loss/duplicate |

The artifact binds exact serialized hashes for S0, S1, U0 qualification,
dataset/evaluator parity, direct add-episode contract, retrieval contract and
policy freeze, adapter identity, Reader-v2 contract/freeze, and the current
role snapshot. It stores no raw question, answer, prompt, model output, or
secret.

## 3. Runtime evidence disclosure

S0 declares construction repository revision:

```text
aa55da1ecc13d006e8b8e4f54579b1ea8c3db2df
```

The S1-bound runtime source contains revision constant:

```text
6e2312b85c2ae9a31f629f24493b79d8b02eab1a
```

That constant did not enter the OpenAI-compatible request and cache was
disabled, so this does not invalidate the completed 49/49 S1 construction.
It does mean the existing evidence is a declared expected configuration, not
an independent current live attestation. The freeze records:

```text
construction_revision_evidence = CONFLICT_DISCLOSED
s4_live_preflight_required      = true
```

No service was called during S3. The next live stage must verify the current
served model/runtime identity before receiving one-shot authority.

## 4. TDD evidence

The initial test failed because `paper_eval.s3_native_v2_freeze` did not yet
exist. The minimal implementation then passed focused and full offline suites.
A second RED/GREEN cycle verified the additive current pointer while retaining
the historical S2 stop ledger.

```text
Initial RED                         1 collection error
S3-v2 focused GREEN               24 passed
Pre-seal full offline GREEN      475 passed
Production pointer RED          1 pass, 1 fail (pointer absent)
Production pointer GREEN          2 passed
git diff --check                   passed
```

Evidence hashes:

```text
logs/TDD_RED_S3_NATIVE_V2_FREEZE_20260814.xml
62e79617ef527bdb4d599e905364cf5703ced18d93362f74af933d637d5fca8d

logs/TDD_FOCUSED_GREEN_S3_NATIVE_V2_FREEZE_FINAL_20260814.xml
5e7df2789d52cf17a014dfec39d8cc5679d103c220134fb81995a8e8e11d9653

logs/TDD_FULL_OFFLINE_GREEN_S3_NATIVE_V2_FREEZE_PRESEAL_20260814.xml
c9f84619c97a744b834d84127e281873fd139565a74505ad8a8f48024492f85b

logs/TDD_RED_S3_NATIVE_V2_PRODUCTION_POINTER_20260814.xml
886b5fc4c43357f8ca8792e53b84d7c0ad80cfb4b84b1759a11ba2c3473258d7

logs/TDD_GREEN_S3_NATIVE_V2_PRODUCTION_POINTER_20260814.xml
81e64855bba12721feaaebb56b9d907c0b740c59aee5bc75bb9f90fe36e88119
```

## 5. Claims and non-claims

Supported:

- Native construction, selected Episode retrieval, Reader-v2, Judge, dataset,
  and current role snapshot are hash-bound as one configuration.
- U0/A0/P*/M* are required to share the same retrieval/Reader/Judge policy.
- Reader-v2 integration compatibility passed without using QA as a gate.

Not supported:

- Historical S2 quality did not pass; the direct result remains
  `REVIEW_REQUIRED`.
- The Reader-v2 canary is not a Native quality estimate and is not mergeable
  into PILOT/FINAL.
- CoN is not claimed to repair the prior failed item.
- No S4, PILOT, MemBind efficacy, or paper headline claim is authorized.

## 6. Primary artifacts

```text
artifacts/paper_eval/native/NATIVE_BASELINE_V2_FREEZE.json
SHA256 3e935af2cb353fb59c4cf58ddec7e44a73387f88410d805324636b76daf2d5e6
payload b05720b834e53f2ab8172249b445afddc37ac8356f19674625e1129823c19da7

runtime/CURRENT_STAGE_STATUS.json
SHA256 3cb7edad4bab3ac6fe961a3d9e8768cbb962cf61cf946cb7e0015d74c0edc26d
```

Current next action:

```text
S4_OFFLINE_GATE_DESIGN_AND_TESTS
```

S4 live and PILOT remain false until their own evidence and authority exist.
