# V3 blocker status update

Generated: 2026-08-09

## State transition

The authoritative stage remains `V3`. It did not advance because
`v3_smoke_002` failed during M0 structured extraction before M2 could start.
The machine state is now:

```text
status: blocked_v3_structured_output
current_blocker: v3_smoke_002_m0_structured_output_failure
```

M1 remained forbidden and did not run. V4, V5, V6, and future work remain
forbidden. The failed attempt and both partial model-oracle caches must not be
reused for a pass claim.

## TDD evidence

The state contract was written and observed failing before the state and plan
were updated. The expanded contract also guards the human-readable execution
header, global resume memory, failure-report hash, and clean service-preflight
hash.

| phase | artifact | SHA256 | outcome |
|---|---|---|---|
| initial red | `artifacts/tdd/v3_failure_state_red_065.log` | `de80f165ffe15125411605511261b7b0c1958095a69ed7072e102d5fe5adbf2e` | 1 expected failure |
| expanded red | `artifacts/tdd/v3_failure_state_expanded_red_066.log` | `e41c725a6526f619f3c89c02c6721c4423af484d86402a467cc00281dae09a25` | 4 expected failures |
| intermediate green | `artifacts/tdd/v3_failure_state_green_067.log` | `0d709812efaa82f477123e172bffef2fb4826b3d79f578bed8a81547a10a1658` | 4 passed |
| preflight-evidence red | `artifacts/tdd/v3_preflight_evidence_red_068.log` | `28518c5da032c09d1ccca913735a3c7a3b7db9e97294cd63f82d140d8205b10e` | 1 expected error for missing state key |
| final focused green | `artifacts/tdd/v3_failure_state_green_069.log` | `f97df916e017eba991f71fe894d8faad309a34b8126b84baaeaae676729fca3c` | 4 passed |
| full mainline regression | `artifacts/tdd/v3_blocker_full_regression_green_070.log` | `6be85ceb90f0436accaf75de967c60ba88784578714ab6d19aae73c3cac547b8` | 222 passed |

The full regression used:

```text
.venv/bin/python -m unittest discover -s tests -q
```

It did not discover or execute the quarantined GPT-5.5 temporary tree, call a
model endpoint, or mutate Neo4j.

## Evidence and classification

The immutable detailed failure report is
`artifacts/diagnostics/v3_smoke_002_failure_report_20260809.md`, SHA256
`060e59eeb5e68015f8b0a022b5e266e19be15dd16dcac7fe240e7c20e8a5b09e`.
The clean preflight is
`artifacts/environment/v3_remote_service_preflight_20260809_initfix.json`,
SHA256
`f8ae2eeb28e5d38f6aae438e656def9eec9ecb0000fd9439705e30cd8aaf14a0`.

The follow-up offline diagnosis is
`artifacts/diagnostics/v3_smoke_002_structured_failure_diagnosis_20260809.md`;
the machine-readable retry/schema analysis is
`artifacts/diagnostics/v3_smoke_002_structured_failure_diagnostic_20260809.json`.
It proves four identical 2048 -> 8192 truncation trajectories and finds the
unbounded `extracted_entities` array, while retaining the unresolved backend
attribution.

The supported classification is an upstream/model structured-output failure
under the frozen Graphiti request. Both allowed completion budgets ended with
`finish_reason=length`. Current evidence does not distinguish vLLM guided
decoding configuration, Qwen model behavior, or another request/runtime
interaction. It is not a service-down interruption, an oracle miss, an M0/M2
semantic divergence, or a database-cleanup failure.

## Only allowed next action

Perform offline diagnosis and collect service-side guided-decoding/runtime
configuration evidence. Do not start `v3_smoke_003` unless compatibility with
the frozen protocol is demonstrated, or an explicit protocol deviation is
approved. Do not change Graphiti prompts, schema, decoding policy, model,
completion budgets, or retry count merely to make the smoke pass.
