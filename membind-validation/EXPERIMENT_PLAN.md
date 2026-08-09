# MemBind Basic Validation: Current Validation v1.2 Execution Plan

This is the concise execution overlay for
`../MemBind_CURRENT_VALIDATION_PLAN_v1.2.md`. The current plan controls task
order. `../MemBind_basic_validation_experiment.md` remains the source for frozen
models, data, methods, metrics, and decision thresholds.

```text
current_stage: V3
current_blocker: v3_smoke_002_m0_structured_output_failure
current_action_scope: blocked_waiting_for_explicit_protocol_deviation
next_allowed_action: no further live execution; retain the structured-output blocker and wait for an explicit protocol deviation or evidenced service-side correction; v3_smoke_003 remains forbidden
forbidden_until_pass: V4/V5/V6/future_work
```

The single authorized frozen public-path probe has now been consumed. Artifact
`artifacts/environment/v3_actual_schema_compatibility_probe_20260809_005_fresh_restart.json`
reproduces all four historical `2048 -> 8192` truncation pairs byte-for-byte at
the historical 5795 prompt tokens. The sanitized post-request restricted-log
evidence records 8/8 HTTP 200 completions and no server error, so connectivity
is not the blocker.
The configured structured-output backend remains `auto`, while the
request-selected backend is still unobserved.
No additional probe, full smoke, construction service change, V4, V5, or V6
execution is authorized.

Machine-readable state is in `CURRENT_STATE.json`. Historical smoke failures do
not create new tasks; their retained evidence index is
`artifacts/history/SMOKE_HISTORY.md`.

Remote model-host inspection is restricted to
`ssh zju-liuyi '<forced-command>'` with
`remote_scope: /home/lhx/liuyi/**` and
`allowed_forced_commands: status/list/read/tail/follow`. These operations are
read-only. An ordinary shell, access outside that scope, forced-command bypass,
and privilege expansion are forbidden. Remote write permission must be
explicitly reported as necessary and enabled in the restricted script before
any modification is attempted.

`v3_smoke_002` failed during M0 structured extraction after both frozen
completion budgets ended with `finish_reason=length`; M2 never started. Its
prompt and embedding caches are partial and must not be reused. The immutable
failure report is
`artifacts/diagnostics/v3_smoke_002_failure_report_20260809.md`. A new live
attempt is forbidden until offline evidence demonstrates service compatibility
under the unchanged protocol, or an explicit protocol deviation is approved.

Offline diagnosis is persisted in
`artifacts/diagnostics/v3_smoke_002_structured_failure_diagnosis_20260809.md`
and the redacted machine-readable analysis in
`artifacts/diagnostics/v3_smoke_002_structured_failure_diagnostic_20260809.json`.
It proves four identical `2048 -> 8192` truncation trajectories and identifies
the installed extraction schema's unbounded `extracted_entities` array, without
claiming a backend root cause. The corrected no-generation metadata probe is
`artifacts/environment/v3_vllm_metadata_probe_20260809_attempt03.json`; direct
version/models/health checks pass, while `/server_info` is not enabled. The
first source-0/source-1 probes incorrectly called the private generation method
and omitted the public wrapper's language instruction; their `-43` token result
and runtime-drift diagnosis are invalidated. The corrected public path reports
the historical 5795 prompt tokens and reproduces all four exact
`2048 -> 8192` failure pairs byte-for-byte. The current blocker is therefore
the confirmed structured-output failure. `v3_smoke_003` remains forbidden until
the deployed backend/config is evidenced and the same public-path probe parses,
or an explicit protocol deviation is approved.

After the service was restored, the read-only direct metadata probe
`artifacts/environment/v3_vllm_metadata_probe_20260809_attempt04_restored.json`
again passed version/models/health but `/server_info` remained 404. Its
classification is `service_restored_backend_config_unavailable`: availability
is restored, while the current `evidence_collection_only` gate is unchanged.
No generation or compatibility probe was run.

Restricted startup evidence is now persisted as
`artifacts/environment/v3_construction_runtime_evidence_20260809.json`. It
proves the configured backend is `auto` and the restarted log snapshot contains
no generation. Classification:
`configured_backend_auto_fresh_service_no_generation_observed`. The authorized
probe against that fresh runtime then reproduced the historical truncation in
`005_fresh_restart`; the detailed conclusion is persisted in
`artifacts/diagnostics/v3_fresh_runtime_compatibility_failure_report_20260809.md`.
The actual request-selected backend remains unobserved. Further live execution
requires an explicit protocol deviation or an evidenced service-side correction.

## Frozen Topology

Machine A serves both OpenAI-compatible model endpoints:

- construction: http://10.87.5.247:8000/v1/, model qwen3-32b-fp8;
- embedding: http://10.87.5.247:8001/v1, model qwen3-embedding-0.6b;
- credentials are loaded only from the ignored project `.env` file.

Machine B runs Graphiti v0.29.3 at commit `021d3a5`, the replay driver, and
Neo4j Community 5.26 locally without Docker. The construction runtime is vLLM
0.26.0 with `max_model_len=40960`; the embedding dimension is 1024. M0, M1,
and M2 share the same endpoints, prompts, schema, decoding, database settings,
and construction concurrency cap of 8. Batch Invariance remains off/default.
`M0` remains the internal method ID for artifact compatibility; its report label
is `Deterministic-Graphiti-Serial`, meaning Graphiti v0.29.3 with the same
deterministic candidate-ordering adapter applied to M0/M1/M2. It is not claimed
to be untouched upstream Graphiti.

The previously approved structured-output compatibility rules remain frozen:
the first completion budget is 2048, one shared bounded parse-truncation retry
may use at most 8192, and single-episode `episode_indices` is constrained to
`[0]`. Input prompts are never truncated.

## TDD Protocol

Every code or instrumentation change follows this sequence:

1. Add the smallest contract or regression test.
2. Run it and persist the expected red output under `artifacts/tdd/`.
3. Implement only the behavior required by that test.
4. Run the focused green tests.
5. Run `.venv/bin/python3 -m unittest discover -s tests -q` before a live gate.
6. Persist the test command, counts, result, and relevant artifact hashes.

No live stage starts while its unit/integration gate is red. A failed live run
is immutable and a replacement always uses a new run ID.

## Single-Line State Machine

```text
V1 Correctness nondeterminism closure
  -> V2 Correctness oracle freeze
  -> V3 Full M0/M2 correctness smoke
  -> V4 Deterministic Graphiti calibration + minimal profiling
  -> V5 M1 one-instance oracle-replay smoke
  -> V6 Formal evaluation
  -> V7 Analysis + validation verdict
  -> STOP
```

Skipping a stage is prohibited.

## V1 Closed Contract

The existing source-5 snapshots prove that some equal logical edge texts and
query paths have different vector hashes, while logical graph size and
full-text hashes remain stable. They do not contain raw vectors, so they cannot
alone produce cross-run cosine, L2, or max-absolute deltas.

V1 is a retained-artifact closure. Reuse the old artifacts for logical keys,
embedding hashes/dimensions/norms, saved membership/order, and prompt evidence.
The old vectors cannot be reconstructed; a new live sample would be a different
execution and would not recover them. V1 therefore must not call either model,
Neo4j, or any live service, and must not run a six-episode or full trace.

Persist the unavailable numerical fields exactly as
`not_computable_from_retained_artifacts` for cross-run cosine, L2, and
max-absolute difference. Claim exact input equality only where retained bytes or
an exact input hash support it; otherwise record `not_available`. The conclusion
must distinguish bitwise variation from an established or unestablished effect
on ranking/top-K/prompt construction.

Required output:

```text
artifacts/diagnostics/embedding_nondeterminism_source5.json
```

V1 ends after this single analysis whether or not numerical drift changes
top-K. Live embedding bitwise identity is not a later correctness prerequisite.

## V2 Model Oracle

Correctness freezes every model-derived output used by the frozen path:

```text
M0 capture: LLM response + embedding vector
M1 read-only replay: same M0 oracle
M2 read-only replay: same M0 oracle
```

LLM or embedding misses always stop before live fallback. An M1 miss is an
`execution_path_divergence` with lifecycle status `completed_with_divergence`;
its `final_semantic_parity = not_evaluable_due_to_oracle_miss`, so it does not
claim a final graph error. An M2 miss is a correctness failure and blocks live
performance. Neo4j state, candidate retrieval, entity/edge resolution,
invalidation, and commits remain live on each method's fresh graph and are never
replayed. M0 diagnostics align by prompt name, source sequence, invocation
ordinal, and call-site identity where available, not by a vague nearest record.

The embedding namespace contains served model ID plus either an
endpoint-reported revision or an operator-supplied immutable deployment
fingerprint, along with dimension, dtype, pooling, normalization/instruction,
and input-transform configuration. Its source manifest is
`artifacts/environment/embedding_model_fingerprint.json`; an alias, URL, or
behavioral probe cannot masquerade as checkpoint identity. Batch composition is
excluded from the item key, so `create(["x"])` and `create_batch(["x"])` share
the exact single-item UTF-8 record.

Cross-encoder status is **expected not invoked; measurement decides**. Audit the
actual `rank()` path over construction and frozen final retrieval, persist
`rank_call_count` and safe input hashes to
`artifacts/diagnostics/model_oracle_audit.json`, and write `not_invoked` only if
the count is zero. A nonzero count blocks V2 until that oracle is frozen.

### Bounded V2 pilot boundary

Before V3, the only live integration allowed in V2 is:

```text
M0 capture -> M0 read-only replay
```

Run it with:

```text
.venv/bin/python src/replay_driver.py v2-oracle-integration \
  --attempt v2_oracle_integration_001
```

The pilot uses one fixed single-episode integration instance and checks zero
replay LLM/embedding calls, zero cross-encoder calls, fresh Neo4j cleanup, equal
graph/retrieval outputs, and unchanged prompt/embedding cache hashes. It does
not run M1 and does not substitute for V3's full M0 -> M2 smoke. A runtime
namespace field without actual remote argv, startup-log, or deployed-config
evidence remains in `unresolved_fields` and blocks the live gate; local templates
and external checkpoint references are provenance, not runtime proof.

## V3-V5 Gates

V3 runs one full M0 capture followed by one full M2 read-only replay on the
existing smoke instance. It requires zero oracle misses/fallback calls,
exactly-once/source mapping parity, canonical graph parity, retrieval guardrail,
and post-run database cleanup. M1 does not run in V3.

V4 uses only the four frozen M0 calibration instances for both DELTA_MS and a
coarse deterministic-Graphiti profile: total, extraction, embedding/search,
resolution/invalidation, DB publication, and unclassified. It records interval
boundaries and basic LLM/embedding/DB counts without a concurrency/load sweep,
GPU kernel profiler, or concurrent network probes.

One model-free guardrail remains. A fixed oracle-replay prefix uses four
counterbalanced OFF/ON pairs for each of M0 and M2, requires semantic parity,
and reports per-method `median(on)/median(off)-1` plus the percentage-point
difference. Method-specific overhead above 5% blocks formal performance; this
is a preregistered Pilot engineering gate. Because the method label is already
`Deterministic-Graphiti-Serial`, the current Pilot does not run an upstream
semantic guardrail.

V5 reuses the V3 M0 oracle for one M1 read-only replay. A first oracle miss ends
as `completed_with_divergence` and proves only execution-path divergence; a
fully matched replay can additionally compare final graph/retrieval semantics.
Completion/source order is diagnostic only. M1 performance is measured only in
V6's live lane.

The shared deterministic presentation contracts remain frozen across every
method and lane:

```text
logical_content_ascending_before_top_k
logical_node_content_ascending_before_top_k
logical_content_ascending_after_top_k
logical_content_ascending_before_candidate_id
```

They may not be extended to chase another correctness miss unless a separately
approved protocol deviation proves upstream physical instability requires it.

## V6 Formal Lanes

Correctness lane:

```text
8 instances x (M0 capture + M1 replay + M2 replay)
= 24 correctness runs
```

Performance lane:

```text
8 instances x 3 methods x 2 repeats
= 48 live runs
```

The formal total is 72 runs. Correctness timings are never reported as
performance. Performance runs use live LLM and live embedding, start with a
cold per-run application embedding cache, and never use the persistent oracle.
They do not require cross-run bitwise-identical embedding vectors.

Performance order uses balanced blocks keyed by `(question_id, repeat)`, with
M0/M1/M2 placed close in wall-clock time under seed 20260806. A clear
infrastructure failure retains and invalidates the old primary block; after
recovery the executor MUST rerun the entire three-method block with a new block
ID. A treatment-induced overload or method failure remains a method result and
is never relabeled infrastructure noise.

All 24 correctness runs finish first. The live lane starts only after M2 reaches
8/8 parity with zero oracle miss/fallback; M1 correctness divergence remains an
outcome and does not block performance. Each of the 16 performance blocks is
contiguous, and the six method permutations differ in use count by at most one.
Infrastructure replacement blocks are appendix-only; primary statistics use the
first complete eligible block. Both repeats retain and report their preregistered
relative gap; each gap above 10% receives a descriptive `stability_warning`, and
the count/rate is reported. It does not trigger a result-directed third repeat.

Live E2E timing retains the real same-LAN path under `NO_PROXY`, the same
endpoints, lightweight pre-run endpoint health, known service-ownership status,
and transport-failure recording. It performs no RTT subtraction and does not
restore the historical 100/20 probe or high-frequency telemetry campaign.

## V7 Verdict

The final report answers only deterministic-Graphiti bottleneck, M2 semantic parity, M2 live
performance, and whether M1 is sufficient. M1 semantic evidence comes from the
same frozen M0 oracle; M1 live speed comes from the performance lane.
Execution-path divergence and final semantic divergence are reported separately;
completion/source-order changes alone establish neither.

Write `artifacts/final/VALIDATION_REPORT.md`, select GO, INCONCLUSIVE, or NO-GO
using the frozen thresholds, and stop. No later mechanism is implemented by
this plan.
