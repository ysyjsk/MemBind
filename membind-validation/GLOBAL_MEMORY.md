# MemBind Global Memory

This file is the compact memory for the validation mainline. It records the
authoritative resume point and the boundaries that future agents must preserve.

## Mainline validation memory

- `CURRENT_STATE.json` is the machine-readable authority. Active protocol:
  `current-validation-v1.3`; resume point `H0`; status
  `h0_protocol_accepted_harness_not_implemented`; current classification
  `late_discovered_pre_freeze_host_compatibility_failure`; action scope
  `h0_offline_tdd_and_harness_only`; `live_h0_candidate_authorized=false` and
  `v3_smoke_003_retired=true`.
- `EXPERIMENT_PLAN.md` is the execution-facing plan for the frozen vLLM/Qwen
  validation lane.
- `../MemBind_CURRENT_VALIDATION_PLAN_v1.3.md` is the authoritative
  human-readable overlay for stage ordering and gates. v1.2 is immutable
  historical protocol evidence.
- Historical blocker ID `v3_smoke_002_m0_structured_output_failure` and
  `artifacts/environment/v3_actual_schema_compatibility_probe_20260809_005_fresh_restart.json`
  remain `historical_negative_host_qualification_evidence`. Reclassification
  never changes the raw failed events or their hashes.
- The sanitized forced-command evidence in
  `artifacts/environment/v3_construction_runtime_evidence_20260809.json` proves
  the restarted V1 engine is configured with structured-output backend `auto`
  and had no generation in the startup-log snapshot. The single authorized
  fresh-runtime probe `005_fresh_restart` has since reproduced the exact
  historical truncation. The immediate action scope is now
  historical `blocked_waiting_for_explicit_protocol_deviation` state. v1.3
  supersedes its action gate but preserves all sanitized evidence.
- The only next work is offline H0 registry/split/context/payload/schema/semantic
  harness TDD. A live Q1 request requires a separate explicit state gate after
  focused and full regression plus manifest review.
- Live H0, V2-R, V3-R, V4-V7, P1-P4, and future work remain forbidden.
- Mainline construction uses Qwen3-32B-FP8 through vLLM 0.26.0 at the frozen
  internal endpoint. Embedding uses the frozen Qwen3-Embedding-0.6B endpoint.
  Its verified deployment dtype is BF16; the old FP16 config placeholder is
  invalid.

## Protocol v1.3 invariants

- `c6853660` is an exposure-quarantined historical regression canary. It never
  participates in candidate selection or held-out formal evaluation. H0 uses
  only the four calibration IDs from `frozen_split_v1_3.json`. That manifest is
  replayed by `src/dataset_v1_3.py`; the immutable v1.2 generator is untouched.
- Q0 is never rerun. Q1/Q2/Q3 are immutable content-addressed delta specs over
  `configs/h0/shared_host_base_v1_3.json`, not runnable manifests. All unresolved
  base hashes must be closed into a resolved manifest before a live gate.
  Selection is first-passing and never observes performance.
- Q0-Q3 keep `seed=20260806`. H0-A performs three repeated bounded trials, not
  statistically independent samples. Q0 and Q1 use different qualification
  wrappers, so their result is not a causal budget-only A/B comparison.
- Every request records requested/effective completion budget. Q2/Q3 must prove
  `top_k/min_p` entered the payload. Q3 is explicit and must inject the
  effective shim schema, not the unmodified upstream schema.
- Parse/Pydantic success alone is insufficient. A pre-candidate,
  calibration-only semantic-invariant manifest rejects valid-but-empty,
  constant, blank-name, duplicate-name, or source-invalid output.
- H0 cannot write formal oracles. V2-R creates a content-addressed namespace
  bound to the qualified host manifest. Correctness uses capture/read-only
  replay; performance uses the same live model stack with response replay off.
- U0/D0 representativeness and quality-feasible M1/M2 tuning occur only after
  V3-R. C8 is an `iso-cap` comparison, not a claim of equal observed usage.
- The current Pilot does not support a General Agent Memory Runtime claim.
  P1-P4 remain `authorized=false` until a V7 GO and a new protocol.

## Mainline exclusion fence

- `gpt55_temporary/**` and every GPT-5.5/LabForge compatibility artifact are
  quarantined, out-of-scope history. Mainline work MUST NOT import, execute,
  test, cite, or copy code or evidence from that tree.
- Temporary GPT-5.5 results are not V3/V4/V5/V6/V7 evidence and do not satisfy
  any construction-model, correctness, performance, or service-health gate.
- Do not inspect the quarantined implementation for design guidance while
  executing the mainline. Its only relevant fact is that it is excluded.
- A mainline run that selects a non-vLLM construction provider is protocol
  invalid and must fail closed before model or database mutation.

## Restricted model-host access

- All remote inspection uses exactly `ssh zju-liuyi '<forced-command>'`.
- `remote_scope: /home/lhx/liuyi/**`; never access, probe, or modify any other
  `/home/lhx` path or any system directory.
- `allowed_forced_commands: status/list/read/tail/follow` and all are read-only.
- An ordinary shell, forced-command bypass, privilege expansion, and `/proc`
  access are forbidden.
- Remote write permission is absent. If a task genuinely requires a change
  under the allowed scope, report it first and wait for an explicit extension
  to the restricted script; never work around the restriction.

## Known V3 attempts

- v3_smoke_001 is an interrupted V3 attempt caused by remote model shutdown. It is not a correctness result.
- Do not reuse partial v3_smoke_001 prompt cache, embedding cache, or trace files for a pass claim.
- The pause report is artifacts/diagnostics/v3_smoke_001_pause_report_20260808.md.
- `v3_smoke_002` failed in M0 because the structured response was truncated at
  both permitted completion budgets. M2 did not start, and this is not an M0/M2
  semantic-parity result.
- Its blocker ID remains `v3_smoke_002_m0_structured_output_failure`.
- Do not reuse the partial `v3_smoke_002` prompt or embedding cache. Its failure
  report is
  `artifacts/diagnostics/v3_smoke_002_failure_report_20260809.md` with SHA256
  `060e59eeb5e68015f8b0a022b5e266e19be15dd16dcac7fe240e7c20e8a5b09e`.
- Do not start `v3_smoke_003` until frozen-protocol service compatibility is
  demonstrated or an explicit protocol deviation is approved.
- Offline diagnosis proves four bitwise-identical `2048 -> 8192` truncation
  trajectories and an unbounded `ExtractedEntities.extracted_entities` array;
  it does not prove the deployed guided-decoding backend or a schema root cause.
- The redacted diagnosis artifacts are
  `artifacts/diagnostics/v3_smoke_002_structured_failure_diagnosis_20260809.md`
  and
  `artifacts/diagnostics/v3_smoke_002_structured_failure_diagnostic_20260809.json`.
- Metadata attempt02 used an invalid proxy route and is not service evidence.
  Direct attempt03 proves vLLM 0.26.0, the expected alias/root/max context, and
  healthy version/models/health endpoints; `/server_info` is disabled.
- After the service restoration,
  `artifacts/environment/v3_vllm_metadata_probe_20260809_attempt04_restored.json`
  again passed the direct version/models/health checks while `/server_info`
  remained 404. Classification:
  `service_restored_backend_config_unavailable`. No generation endpoint was
  called, so the backend evidence gate did not change.
- The first source-0/source-1 controls bypassed the public `generate_response`
  wrapper and omitted its language instruction. The resulting construction
  runtime-drift artifact and proposed blocker
  `v3_construction_runtime_identity_drift_after_smoke_002` are an invalidated
  diagnostic, not a current blocker.
- Corrected artifact
  `artifacts/environment/v3_actual_schema_compatibility_probe_20260809_004_reclassified.json`
  proves exact historical truncation reproduction without another model call.
- The single authorized fresh-runtime artifact
  `artifacts/environment/v3_actual_schema_compatibility_probe_20260809_005_fresh_restart.json`
  again reproduces all four exact `2048 -> 8192` pairs at 5795 prompt tokens.
  Post-request evidence is in
  `artifacts/environment/v3_post_compatibility_runtime_evidence_20260809.json`;
  the detailed result is in
  `artifacts/diagnostics/v3_fresh_runtime_compatibility_failure_report_20260809.md`.
- `v3_smoke_003` is permanently retired rather than reopened under v1.3. The
  historical next state was `blocked_waiting_for_explicit_protocol_deviation`;
  the active v1.3 state now permits only offline H0 TDD. A new correctness smoke
  uses a V3-R namespace only after H0 and V2-R pass.

## Maintenance notes

- New diagnostic scripts should include a module docstring explaining whether they are mainline or temporary.
- New tests should include a short class docstring when they protect protocol boundaries rather than ordinary implementation details.
- Diagnostics may persist short credential fingerprints but must never persist raw API keys or Authorization headers.
