# V3 Smoke 001 Pause Report

Generated: 2026-08-08

## Status

- Stage: V3 full correctness smoke.
- Attempt: `v3_smoke_001`.
- Question id: `c6853660`.
- Current result: infrastructure-interrupted / inconclusive.
- Reason: M0 capture failed with `APIConnectionError('Connection error.')` after the remote vLLM service was stopped.
- M2 replay did not start, so this run provides no M2 correctness/parity result.
- No V4, V5, V6, M1, calibration, performance, or future-work lane was run.

## Artifacts

- V3 summary: `artifacts/smoke/v3_smoke_001.json`
  - SHA256: `2825875de46dab9584c40666192586e5badd0950306d04fe4fc413cda3143ffa`
  - `ok=false`
  - error: `ExperimentRunFailed("experiment run v3_smoke_v3_smoke_001_M0_c6853660 failed: APIConnectionError('Connection error.')")`
- M0 run status: `artifacts/runs/v3_smoke_v3_smoke_001_M0_c6853660.json`
  - SHA256: `17f47a56057a2c30fe6be8734233af681b31a0814c29c3da4c45be25bfb88e64`
  - `status=failed`
  - started: `2026-08-08T03:39:11.298671+00:00`
  - finished: `2026-08-08T04:14:06.752528+00:00`
- M2 run status: not created.
- Prompt cache: `artifacts/prompt_cache/v3_smoke_v3_smoke_001_c6853660.jsonl`
  - SHA256: `0bea942c944b14646a46a6f9725b12806d5f84f7daa7f7d9ed868eae490df76c`
  - line count: 349
- Embedding cache: `artifacts/embedding_cache/v3_smoke_v3_smoke_001_c6853660.jsonl`
  - SHA256: `30b4c78e5096ae1193a1a34b84fe8f77b6f7cb6dc109529f6689a91f2bd7cf8c`
  - line count: 290
- M0 trace: `artifacts/traces/v3_smoke_v3_smoke_001_M0_c6853660.jsonl`
  - SHA256: `3802c707144d1b6d03a40132658da136019bc5e74c36d0c52a75f0908650df3c`
  - line count: 28
- Live log: `artifacts/tdd/v3_smoke_live_037.log`
  - SHA256: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
  - bytes: 0

## TDD Work Completed Before Live Attempt

- Added a failing test proving V3 must not run M1:
  - `artifacts/tdd/v3_smoke_no_m1_red_033.log`
  - SHA256: `64efee8735223ac68128ce3b329b50d56b4291329021f5c4ed5414ac1b24019f`
- Implemented dedicated `v3-smoke` entrypoint.
- Focused green:
  - `artifacts/tdd/v3_smoke_no_m1_green_034.log`
  - SHA256: `543ff3f24a4d25a940047e6a97621bc11ef060bf28a89cb1d01efc30086db08d`
- Related green:
  - `artifacts/tdd/v3_smoke_related_green_035.log`
  - SHA256: `0725dcc3a785acebde2626a5b12492cf3ad0fedc2a8bb9d9a09bf3a77f300ba4`
- Full regression green:
  - `artifacts/tdd/v3_smoke_full_regression_green_036.log`
  - SHA256: `2006cc27f2ffd4243108c2295c90c7b34c1e73447760e9d79ce003582d2c2a52`
  - 218 tests passed.

## Interpretation

This attempt is not a MemBind correctness result. It is an infrastructure interruption during M0 capture. The partial prompt cache, embedding cache, and trace show that the V3 run had begun and was making progress before the remote model service became unavailable. Because M0 did not complete and M2 did not run, no graph parity, retrieval parity, or replay-oracle claim can be made from this attempt.

## Next Valid Action

After the remote vLLM services are stable again, start a fresh V3 attempt with a new attempt id. Do not reuse the partial `v3_smoke_001` oracle cache for a pass claim.
