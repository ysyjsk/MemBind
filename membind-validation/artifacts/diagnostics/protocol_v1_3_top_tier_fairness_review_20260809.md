# Protocol v1.3 Top-Tier Fairness Review

## Outcome

The central correction is accepted with modifications: qualification must precede
freezing, and the first qualified shared host stack must be used unchanged by U0,
D0, M1, and M2. The observed Q0 failure happened before M2, Neo4j, or embedding, so
it is not evidence against the MemBind mechanism. This is a retrospective correction
after observing Q0, not part of the original preregistration.

Audit vocabulary: `accept_modify_reject`. Every literature entry below records a
`source_url`, `accessed_at`, `supported_claim`, and
`unsupported_or_qualified_claim`. Public-source access date is 2026-08-09.

No model, embedding, database, remote SSH, or evaluation workload was run during this
review. No API key, Authorization header, raw prompt, raw response, or environment dump
is persisted.

## Decision Matrix

| Proposal | Decision | Required correction |
|---|---|---|
| Reclassify the V3 blocker | Accept with modification | Preserve historical ID and raw artifacts; add a separate protocol classification |
| Add pre-freeze H0 | Accept | Keep qualification disjoint from evaluation and performance-blind |
| Q1 -> Q2 -> Q3 first passing | Accept with modification | Exact content-addressed manifests, exact diffs, eligibility checks, stop after first pass |
| Use `c6853660` to select candidates | Reject | It is old evaluation data and heavily exposed; retain as historical diagnostic only |
| Treat 3/3 as reliability proof | Reject claim, retain engineering gate | It is a bounded fail-fast canary, not a confidence statement |
| Parse/Pydantic success as complete H0 | Reject | Add a frozen calibration-only semantic utility gate that rejects empty/default-only output |
| Rebuild correctness oracle | Accept | Namespace binds qualified manifest and cannot be populated by H0 |
| Replay correctness, use live model for performance | Accept | This is the strongest causal isolation in the protocol |
| Keep U0 and D0 | Accept with modification | Freeze exact adapter boundary and non-inferiority guardrail |
| Tune M1 and M2 | Accept with modification | Quality feasibility precedes speed selection; fixed objective/tie-break |
| Call C8 iso-resource | Reject wording | Use `iso-cap`; report actual utilization and work volume |
| Execute paper expansion now | Defer | P1-P4 remain unauthorized until V7 GO |
| Claim a general agent-memory runtime now | Reject | Requires abstraction audit plus at least a second architecture |

## Critical Corrections

1. Evaluation leakage: `c6853660` was in the old evaluation set but powered extensive
   debugging. `frozen_split_v1_3.json` preserves the four calibration IDs, quarantines
   that exposed ID, and uses the original SHA256 order to add the next unseen ID. The
   old split remains immutable. This is exposure-based, not performance-result-based.

2. Context feasibility: Graphiti's pinned constructor default is 16384, but a known
   32757-token prompt on a 40960 context leaves 8171 tokens after the frozen 32-token
   margin. Every attempt must report requested and effective budgets; the paper cannot
   claim that every request receives 16K.

3. Q3 schema identity: Graphiti's `json_object` path injects a Pydantic schema. The
   current local `[0]` shim is applied to the response-format schema, not necessarily
   the injected one. Q3 is forbidden until a test proves
   `schema_injected_sha256 == schema_effective_sha256`.

4. Semantic validity: valid JSON can still be a degenerate memory update. H0 must use
   a content-addressed, pre-candidate calibration semantic manifest and reject expected
   non-empty calls that become empty, blank entities, invalid source indices, duplicate
   normalized names, and constant/default-only outputs.

5. Retry semantics: one public Graphiti invocation is a logical trial; every completion
   request is an HTTP attempt. A retry does not become another independent trial.
   Candidate-induced retry fails qualification; independently evidenced infrastructure
   failure reruns the entire stage with a new attempt ID.

6. Baseline tuning: M1/M2 scan C={1,2,4,8} only on calibration. A point must satisfy
   correctness, retrieval, completion, and exactly-once guardrails before it can win on
   median makespan. Exact ties choose the smaller C.

7. Candidate-manifest truthfulness: the first draft called Q1-Q3 complete manifests,
   but they contained only decoding deltas and Q1 silently changed the seed policy.
   The corrected design fixes `seed=20260806` across Q0-Q3, calls the three H0-A
   invocations repeated bounded trials rather than independent samples, and represents
   Q1-Q3 as immutable delta specs over `shared_host_base_v1_3.json`. The base remains
   `live_eligible=false` until all client, prompt, schema, HTTP, retry, semantic, and
   launch hashes are resolved into a complete candidate manifest. Because Q0 used the
   old wrapper, Q0-to-Q1 is not claimed as a causal budget-only A/B experiment.

8. Split reproducibility: the initial v1.3 split listed the right replacement ID but
   pointed at the unchanged v1 generator, which could not reproduce the quarantine.
   `src/dataset_v1_3.py` now verifies the source and immutable legacy-split hashes,
   applies only the exposure quarantine, and replays the original SHA256-ID ordering.
   The old generator is untouched, preserving its historical hash.

## Source Audit

### Graphiti 021d3a5

- source_url: https://github.com/getzep/graphiti/blob/021d3a57d511f21b10adaf7fa923bd5c1fce5e9d/graphiti_core/llm_client/openai_generic_client.py
- accessed_at: 2026-08-09
- supported_claim: constructor default 16384; `json_schema` and `json_object`; explicit
  schema injection in `json_object` mode.
- unsupported_or_qualified_claim: `json_object` is not an automatic switch after a
  failed `json_schema` request. `json_schema` is not strict on every provider.

Pinned README:
https://github.com/getzep/graphiti/blob/021d3a57d511f21b10adaf7fa923bd5c1fce5e9d/README.md

### Qwen3-32B-FP8

- source_url: https://huggingface.co/Qwen/Qwen3-32B-FP8/blob/aa55da1ecc13d006e8b8e4f54579b1ea8c3db2df/README.md
- accessed_at: 2026-08-09
- supported_claim: non-thinking mode suggests 0.7/0.8/20/0 sampling.
- unsupported_or_qualified_claim: this does not establish the historical truncation's
  root cause, and Q1's 16K cap comes from Graphiti rather than Qwen's general advice.

### vLLM 0.26.0

- source_url: https://github.com/vllm-project/vllm/blob/v0.26.0/vllm/config/structured_outputs.py
- accessed_at: 2026-08-09
- supported_claim: `auto` makes opinionated, release-dependent backend choices.
- unsupported_or_qualified_claim: configured `auto` does not reveal the selected
  per-request implementation; `response_format=json_schema` is insufficient evidence.

Auto-resolution source:
https://github.com/vllm-project/vllm/blob/v0.26.0/vllm/sampling_params.py

### Systems Methodology Precedents

- ContextPilot, MLSys 2026:
  https://proceedings.mlsys.org/paper_files/paper/2026/hash/b0131b6ee02a00b03fc3320176fec8f5-Abstract-Conference.html
  It explicitly tunes baselines for performance and accuracy. It supports fair,
  calibration-only tuning, not result-driven configuration fishing.

- Agentix, NSDI 2026:
  https://www.usenix.org/conference/nsdi26/presentation/luo
  It includes vLLM, vLLM-opt, MLFQ and Agentix. This supports a strong baseline ladder.

- Pie, SOSP 2025, DOI 10.1145/3731569.3764814:
  https://doi.org/10.1145/3731569.3764814
  It aligns backends and high-level workflows and performs best-effort optimization.

- DistServe, OSDI 2024:
  https://www.usenix.org/conference/osdi24/presentation/zhong-yinmin
  It uses Poisson arrivals when source datasets lack timestamps, supporting a future
  load-sweep precedent rather than authorizing one now.

- Parrot, OSDI 2024:
  https://www.usenix.org/conference/osdi24/presentation/lin-chaofan
  Its end-to-end chain analysis includes client interaction and request re-entry effects.
  This supports retaining the real network path, but the no-RTT-subtraction rule remains
  MemBind's deployment and causal contract rather than a universal Parrot rule.

### Agent Memory

- source_url: https://export.arxiv.org/api/query?id_list=2606.06448
- accessed_at: 2026-08-09
- supported_claim: the public record is `Agent Memory: Characterization and System
  Implications of Stateful Long-Horizon Workloads`, an arXiv preprint, 2026. Its paper
  supports the Qwen stack and minimal interface adaptations.
- unsupported_or_qualified_claim: public metadata does not independently establish an
  IISWC venue or acceptance state. Its MemoryArena experiment captures per-session
  timing and replays it on a controlled five-second schedule; it is not evidence that
  the experiment was live continuous serving.

### Artifact Guidance

- OSDI 2026 Call for Artifacts:
  https://www.usenix.org/conference/osdi26/call-for-artifacts
- NSDI 2026 Call for Artifacts:
  https://www.usenix.org/conference/nsdi26/call-for-artifacts

Both support concrete claims, quick-start plus detailed reproduction instructions,
dependency/runtime documentation, stable availability, and explicit limitations.
OSDI 2026's available-artifact process should not be overstated as proof that every
claim was independently reproduced.

## Code/Plan Gaps Found

- `replay_driver.py` and `formal_gate.py` still encode 64 runs and omit M1 correctness
  replay, while the active proposal requires 72. V6 remains forbidden until TDD closes it.
- The current H0 candidate harness does not exist and runtime entry points do not yet
  enforce `CURRENT_STATE.json`; live H0 therefore remains false.
- Q2 payload support for `top_k/min_p` and Q3 effective-schema injection are absent.
- Prompt/oracle identity must bind the qualified host manifest before V2-R.
- `replay_driver.py` and `formal_gate.py` still implement the historical 64-run/global-
  shuffle plan. The documents now state 72 correctness-first blocked runs, but this is
  explicitly implementation debt rather than a completed feature.

Closed during this review: `base.yaml` now uses the deployment-verified BF16 embedding
dtype; the execution-facing V3-R/V4/V5 text now requires a new calibration smoke,
U0/D0 representativeness, and quality-feasible C={1,2,4,8} tuning; the v1.3 split is
machine-replayable; candidate files no longer masquerade as complete live manifests.

## TDD Evidence

RED contract test:

```text
membind-validation/tests/test_protocol_v1_3_contract.py
sha256=817ed72cb7095a349b893b3727707d5efacbeea95a6e2790abf2b3a621ba5dce
```

RED output:

```text
membind-validation/artifacts/tdd/protocol_v1_3_document_contract_red_157.log
Ran 17 tests
FAILED (failures=17, errors=0)
sha256=f065caf508ffff20c87dd35ad08c0da55a715e231400036eec8823e4ead3fa24
```

Focused GREEN and final regression evidence are appended only after the synchronized
documents and state satisfy the contracts.

Initial document GREEN:

```text
membind-validation/artifacts/tdd/protocol_v1_3_document_contract_green_159.log
Ran 17 tests
OK
sha256=34dcd2269e48332c856e144a44b5fb89a165e1a823142a4529bfc002484da926
```

The full-regression attempt then exposed 12 stale v1.2-as-current assertions. It is
retained as negative TDD evidence:

```text
membind-validation/artifacts/tdd/protocol_v1_3_full_regression_attempt_160.log
Ran 275 tests
FAILED (failures=12)
```

After preserving all V3 artifact/hash assertions while separating historical and
current state, the focused stale-contract suite passed 33/33. A second RED cycle then
caught the candidate/split/execution-plan artifact inconsistencies:

```text
membind-validation/artifacts/tdd/protocol_v1_3_artifact_contract_red_162.log
Ran 5 tests
FAILED (failures=5, errors=0)
sha256=f4c8ecac7652c5c6b3bd9c128396b88804dba5356fdca8b0e6f5963b5e995389
```

Final integrated focused GREEN:

```text
membind-validation/artifacts/tdd/protocol_v1_3_artifact_and_document_focused_green_attempt_164.log
Ran 55 tests
OK
sha256=c75a3b11da5bbc50b1056621dd695132fd56edde4362b8ed2e20fe04ed2a9b07
```

Full offline regression GREEN:

```text
membind-validation/artifacts/tdd/protocol_v1_3_full_regression_attempt_165.log
Ran 280 tests
OK
sha256=687c1ca19919a029302e0e6eec68596129434a266085cb9aada3c1d776cb3aef
```

This regression still prints a fixture that constructs 64 planned runs. That confirms
the documented V6 implementation debt; it is not evidence that the 72-run runner has
already been implemented. No live endpoint, model, embedding, Neo4j workload, or SSH
operation was used by these protocol-review tests.

After synchronizing the machine-readable evidence index, both verification layers were
run again:

```text
membind-validation/artifacts/tdd/protocol_v1_3_post_state_focused_green_attempt_166.log
Ran 55 tests
OK
sha256=9347c05a9383cba6e49c2c6c69040bbe9c4f5e8e239d6139ba4a70f532b08a24

membind-validation/artifacts/tdd/protocol_v1_3_final_full_regression_green_167.log
Ran 280 tests
OK
sha256=9ba5e8e4edbcb4f683bf581f421c31b9dd6f656a46c9d5e412eec766e4357386
```

## Current Authorization

```text
protocol_version=current-validation-v1.3
current_stage=H0
status=h0_protocol_accepted_harness_not_implemented
current_action_scope=h0_offline_tdd_and_harness_only
live_h0_candidate_authorized=false
v3_smoke_003_retired=true
P1/P2/P3/P4 authorized=false
```

The next allowed action is offline H0 harness TDD. A live Q1 request requires a
separate explicit state transition after focused and full regression, manifest review,
and a frozen calibration semantic-invariant artifact.
