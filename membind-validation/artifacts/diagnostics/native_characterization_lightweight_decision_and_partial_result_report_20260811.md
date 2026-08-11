# Native Characterization Lightweight Decision and Partial Result Report

Date: 2026-08-11

## Decision Verdict

The execution had become too heavy. The research direction was still correct,
but an auxiliary instrumentation-overhead guardrail had displaced the actual
goal of obtaining a Native Graphiti construction breakdown.

The corrected execution policy is now:

- semantic parity and measurement correctness remain hard requirements;
- instrumentation overhead is retained and reported as perturbation evidence,
  but no fixed percentage alone blocks C2 screening;
- no further estimator, qualification-validator, provenance, C0, or fallback
  infrastructure is allowed unless it directly affects C2 measurement
  correctness;
- the only compatibility candidate tried was `json_object`; another parser,
  retry, or structured-output fallback was not added after it failed.

The frozen workplan and canonical freeze remain unchanged. The narrow policy is
persisted in `native_characterization_lightweight_execution_decision_20260811.md`.

## Overhead Evidence Interpretation

- The original C1 qualification remains closed: semantic parity passed and the
  median paired overhead was `1.317%`.
- The later five-pair C2 measurement fixture reported `5.871%`. That artifact
  remains valid under its original method, but its approximately 100 ms arms
  ranged from `-0.434%` to `24.993%`; it does not have enough resolution to
  justify a hard 5% execution cutoff.
- The engineering diagnostic reported a combined/off median of `0.371%`, an
  adapter/base median of `-0.221%`, and approximately `127.6 us` of combined
  fixed wrapper work per synthetic episode. It found no material adapter
  hotspot at the available noise resolution. It is not an estimate of live C2
  overhead.

## Lightweight Preparation and TDD

The unfinished C0 mode/provenance expansion and the additional CPU-clock,
load-average, governor, and estimator-validator expansion were removed. The
remaining implementation only binds the selected structured-output mode from a
safe relative freeze path into U0, the C2 runtime, manifest provenance, and the
verifier.

Evidence retained:

- minimal `json_object` adapter intentional RED, then 14 focused tests GREEN;
- current compatibility path: 61 focused tests GREEN;
- latest cleanup-attribution RED, then 12 cleanup tests and 21 integrated tests
  GREEN;
- final pre-live offline regression: 776 tests GREEN in 93.361 seconds;
- C2-only gate transition: 83 focused tests GREEN;
- post-failure live revocation: 83 focused tests GREEN.

The single derived freeze is
`artifacts/native_characterization/freeze_json_object.json`:

```text
file_sha256    1952fb7cde2fed9b9ef22024a98642de83e7c29aade1144148e5b734953b4b28
payload_sha256 8dd2877d240793bfdba44eafc19e1ceb95b52d0ead99381735422e8b6d7865ba
mode           json_object
```

It differs from the canonical freeze only in its variant labels, creation note,
structured-output mode, current U0 runtime source hash, and derived payload
hash. The canonical freeze SHA256 remains `3bca97e1...e001c`.

## Exact Cleanup

Only `nc-e1e2-400b9b78c2c218df` was eligible for deletion. The helper verified
that this was block 0 in the selected freeze, counted the group before cleanup,
called Graphiti's group-scoped `clear_data`, and required zero residual objects.

```text
before: 38 nodes, 79 relationships
after:   0 nodes,  0 relationships
```

The sanitized evidence is
`artifacts/native_characterization/c2_cleanup_second_json_object_20260811.json`.
This was the pre-run state. A read-only count after the failed fresh attempt
found `56` nodes and `67` relationships in the same group. The namespace is
therefore polluted again, is not reusable, and was not deleted a second time.

## Live Attempt Outcome

Fresh run `c2-c5e5463facb3bce7` used the derived `json_object` freeze and started
from source sequence 0. Episodes 0 through 6 completed with complete telemetry,
zero LLM transport errors, zero embedding errors, zero database errors, and no
retries. Episode 7 then failed and stopped the run as required.

```text
error_code       pydantic_core._pydantic_core.ValidationError
direct phase     edge-resolution
prompt           dedupe_edges.resolve_edge
input/output     964 / 91 tokens
transport status ok
retry count      0
vLLM disconnect  false
```

This is not the earlier `JSONDecodeError`: `json_object` produced parseable JSON,
but one returned object did not satisfy the Pydantic response model. The failed
episode's `add-episode`, `edge-resolution`, and LLM error spans were durably
recorded. No fallback or automatic rerun was attempted.

## Preliminary Completed-Prefix Signal

The following is an invalid-prefix diagnostic, not an official C2 result. It is
based on seven dependent episodes from one incomplete history and zero complete
blocks.

```text
completed episodes          7 / 188
complete blocks             0 / 4
service-time sum            133.718 s
service-time mean            19.103 s
service-time median          10.807 s
service-time range         7.020-41.503 s
```

High-level phase occupancy, computed by summing each episode's interval union:

| Phase | Seconds | Share of service sum |
|---|---:|---:|
| edge extraction | 50.774 | 37.97% |
| node resolution | 29.233 | 21.86% |
| node extraction | 28.820 | 21.55% |
| attributes / summary | 19.151 | 14.32% |
| edge resolution | 3.249 | 2.43% |
| publication | 2.444 | 1.83% |
| previous context | 0.045 | 0.03% |

Nested resource occupancy must not be added to the table above:

| Resource/support interval | Seconds | Share of service sum |
|---|---:|---:|
| LLM logical-call union | 130.219 | 97.38% |
| LLM transport union | 130.160 | 97.34% |
| database union | 0.947 | 0.71% |
| candidate search union | 0.731 | 0.55% |
| embedding union | 0.412 | 0.31% |
| candidate embedding union | 0.285 | 0.21% |

Completed-prefix work volume:

```text
LLM calls / attempts       45 / 45
LLM input / output tokens 220,169 / 4,916
LLM retries / errors       0 / 0
embedding calls / texts    54 / 170
embedding errors           0
DB queries / tx / writes   146 / 7 / 28
DB errors                  0
candidate count            518
```

The first useful signal is therefore not a local Neo4j or embedding bottleneck.
For this prefix, model inference and model-call amplification dominate service
time. The two slowest completed episodes were about 41 seconds, while the median
was about 10.8 seconds. One slow episode used a long edge-extraction response;
the other issued many resolution/timestamp calls. Graph prefix and episode
content co-vary, so this does not establish a causal graph-size trend.

The decomposition opportunity remains unresolved. Extraction is substantial,
but node and edge resolution are also material and may depend on current graph
state. C3 dependency evidence is still required before claiming that the costly
work can be safely prepared in parallel.

## Validity and Stop Boundary

This attempt is invalid, non-mergeable, and non-resumable. It produced no
`e1_breakdown.json`, no complete history block, and no paper-grade C2 claim. The
seven completed episodes may be cited only as a preliminary engineering signal
and as evidence that the measurement path is useful when the model output is
valid.

All live actions are revoked. The current blocker is:

```text
c2_json_object_validation_failure_stop_no_fallback
```

The failed attempt's namespace currently contains 56 nodes and 67 relationships.
Cleanup is not authorized by this report.

The next decision must address structured-output semantic reliability at the
model-serving boundary or explicitly change the research object. It must not
silently add an application parser, retries until success, another compatibility
matrix, or merge any prefix from the three failed attempts.

## Persisted Evidence

- Aggregate diagnostic:
  `artifacts/diagnostics/native_characterization_c2_json_object_partial_diagnostic_20260811.json`
- Root checkpoint SHA256:
  `3f9e0235f4ec814e8b667380a257d1c4ccbcf2fa95b773c0bac8dc6e89f15e31`
- Span stream SHA256:
  `5524c235f9a494400e1b53ffb45049644edf3f4668bc519d875219d984ab1e3e`
- Error stream SHA256:
  `8dc01336b854e5a561e0f44331ea45ab6c87b8539ac5dbb4faa507e9a6f0f76e`
- Outer log SHA256:
  `7341223b6b6df1027607a5db9d74377a146081af291d8f70882461e164babf77`
- Final pre-live offline regression SHA256:
  `c7b48eea8549b991537dfbf26198d14f1ac46cf787c643d5364e2d01764a48c1`
