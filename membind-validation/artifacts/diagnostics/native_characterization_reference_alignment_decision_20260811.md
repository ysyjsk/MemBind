# Native Characterization C2 Reference-Alignment Decision

Date: 2026-08-11

Status: `CLEANUP_ONLY_GREEN`

This decision does not modify
`MemBind_NATIVE_GRAPHITI_CHARACTERIZATION_WORKPLAN_v1.1.md` or add an
experiment, model, parser, retry matrix, structured-output backend, prompt, or
sampling candidate. It authorizes preparation for exactly one fresh C2 only
after exact namespace cleanup has been verified. C2/model live actions remain
denied; the exact state-bound namespace cleanup is the only allowed mutation.

## Corrected premise

The prior claim that formal C2 still used a `2048 -> 8192` project retry path is
withdrawn. C2 U0 already passed `max_tokens=16384`; the old two-budget helper is
a legacy path and does not participate when the primary budget is 16384. The
reference-alignment change therefore must not be described as a token-budget
fix or as the first 16K C2 attempt.

The actual change is narrower: provider-critical structured request creation,
code-fence stripping, `json.loads`, and bounded retry now remain inherited from
pinned Graphiti `OpenAIGenericClient`. The project keeps only:

- the single-episode `episode_indices=[0]` schema constraint;
- `top_p=1.0`, `seed=20260806`, and
  `extra_body.chat_template_kwargs.enable_thinking=false` at the transport;
- transport timing/token telemetry.

No project `_generate_response()` or `generate_response()` override remains.
No project noisy-JSON salvage, Pydantic repair, context probe, or retry-budget
matrix participates in C2.

## Reused upstream implementation

The implementation and tests were checked against primary upstream sources,
not reconstructed from secondary descriptions.

- Graphiti PR #1619, merged 2026-07-27 as
  `94e0d7830a421de2d09cbbe4a1bcd0098619a595`, exposes the generic client's
  `json_schema` default and `json_object` fallback. The PR reports 9 generic
  client tests and 26 MCP factory tests:
  <https://github.com/getzep/graphiti/pull/1619>
- Pinned Graphiti source at commit
  `021d3a57d511f21b10adaf7fa923bd5c1fce5e9d` has SHA256
  `62d198e838030f4e309df2eeb0eb7cf1770e3bb32cbe34c8a9f3ee3dc0ca7705`:
  <https://raw.githubusercontent.com/getzep/graphiti/021d3a57d511f21b10adaf7fa923bd5c1fce5e9d/graphiti_core/llm_client/openai_generic_client.py>
- Its 9-test generic-client reference has SHA256
  `ef0db11b97c97d91ffffdf2f3635daf26b3712aee65f07db285899f27e81ff72`:
  <https://raw.githubusercontent.com/getzep/graphiti/021d3a57d511f21b10adaf7fa923bd5c1fce5e9d/tests/llm_client/test_openai_generic_client.py>
- vLLM v0.26.0 documents OpenAI `response_format.type=json_schema`, direct
  Pydantic `model_json_schema()` use, and structured-output backend `auto` as
  the default. The frozen source contract is
  `artifacts/environment/vllm_0_26_structured_output_contract_20260809.json`:
  <https://raw.githubusercontent.com/vllm-project/vllm/v0.26.0/docs/features/structured_outputs.md>
- Qwen documents `enable_thinking` as a framework-specific, non-OpenAI field
  passed through `extra_body.chat_template_kwargs`. The checked document has
  SHA256 `415e756c5e69f8d3c449ab08d0a7ccaf2d52a767d0be29bc62095cf298d15507`:
  <https://raw.githubusercontent.com/QwenLM/Qwen3/main/docs/source/deployment/vllm.md>

These sources support the thin-shim boundary. They do not prove that the
current model/server envelope can satisfy every nested Graphiti output contract;
that remains the purpose of the one final C2 attempt.

## TDD evidence

The implementation was driven through bounded failing contracts before the
corresponding production changes:

1. Provenance and cleanup RED: 9 tests ran; the missing runtime/shim hashes
   produced 1 error and 2 failures, and stale cleanup attribution produced 1
   failure.
2. Transport telemetry RED: 2 tests ran; the missing transport-error counter
   produced 1 error and the old structured-failure interpretation produced 1
   failure.
3. Derived-freeze RED: both tests failed because the reference-aligned artifact
   did not yet exist.
4. Focused GREEN: 112 tests passed. Log SHA256:
   `d4c97fa253b3adcda9a1f16f37e1199da2c3dd755be6399b871fc17f41bbd031`.
5. Final full offline GREEN: 783 tests passed in 76.631 seconds. Log SHA256:
   `42c439980c7673c67565625d805944f8ac04ec9c1341d5877e0266c82c034bbb`.

Persistent logs:

- `artifacts/tdd/native_characterization_reference_alignment_focused_green_20260811.log`
- `artifacts/tdd/native_characterization_reference_alignment_full_offline_green_20260811.log`

The final C2 manifest now binds `native_characterization_runtime.py` and
`graphiti_native.py` in addition to the runner, measurement adapter,
instrumentation, tracing, phase map, dataset, and selected freeze. The offline
verifier independently re-hashes all of those local execution sources.

## Frozen execution identity

The historical canonical freeze remains byte-unchanged at SHA256
`3bca97e1f531dbd23584dd02248a0cbed783f2153f3c756880826ea0c48e001c`.
The frozen workplan remains byte-unchanged at SHA256
`be3112cc2da4080ce98f9c94f1ab510ba5cc8350dca108a15e304da04c996b5b`.

The derived execution freeze is reference-aligned with explicitly declared
project deviations; it is not byte-for-byte upstream Graphiti behavior. The
retained deviations are the single-episode schema constraint, three frozen
Qwen transport fields, and transport telemetry. The freeze is:

`artifacts/native_characterization/freeze_reference_aligned.json`

SHA256:
`cea700f73f7dc942deeb49195e0a3ca235c35ec51a1c06fdab0edd94738330a7`

It preserves the exact dataset, four C2 block order/namespaces, U0/U0-S
objects, model and embedding identities, Neo4j identity, sampling, and
workplan. It changes only execution-path identity and adds source hashes for
the thin shim, its contract test, the U0 factory, and the pinned Graphiti
generic-client implementation. It selects `json_schema` and requests no fixed
structured-output backend.

## Existing observations and scientific boundary

There is still no valid formal C2 result and no `e1_breakdown.json`. The three
prior attempts are invalid, non-mergeable prefixes:

- `c2-efb58c477f12adf6`: `json_schema`, 10 completed episodes, JSON decode
  failure;
- `c2-723261287e32e182`: `json_schema`, 10 completed episodes, same boundary;
- `c2-c5e5463facb3bce7`: `json_object`, 7 completed episodes, Pydantic shape
  failure during edge resolution.

The latest 7-episode prefix is engineering signal only. It measured 133.718 s
of aggregate service time, mean 19.103 s, median 10.807 s, with LLM interval
union at 97.38% of service sum. Edge extraction was 37.97%, node resolution
21.86%, node extraction 21.55%, attributes/summary 14.32%, edge resolution
2.43%, publication 1.83%, database union 0.71%, and embedding union 0.31%.
These values cannot be reported as formal C2, cannot establish dependency
classification, and cannot be merged with another attempt.

## Cleanup and one-attempt boundary

The only cleanup target is group
`nc-e1e2-400b9b78c2c218df`, polluted by
`c2-c5e5463facb3bce7` under
`artifacts/native_characterization/freeze_json_object.json` (SHA256
`1952fb7cde2fed9b9ef22024a98642de83e7c29aade1144148e5b734953b4b28`).
The last diagnostic count was 56 nodes and 67 relationships, but the cleanup
must measure fresh pre-counts rather than assume those values. It must use the
scoped `graphiti.clear_data(driver, group_ids=[target_group])` primitive and
verify exact post-counts of 0 nodes and 0 relationships. No other namespace may
be modified.

The production cleanup entrypoint reads this cleanup-only grant from
`CURRENT_STATE.json`, verifies the exact source freeze path and SHA256 before
database I/O, and rejects caller-selected source identities. Only after its
evidence is durable may current authority grant exactly
`native_characterization_c2`. The fresh run must:

- use a new `c2-[0-9a-f]{16}` ID;
- use the derived reference-aligned freeze;
- start from source sequence 0 with no resume or prefix merge;
- preserve one durable checkpoint and sanitized progress event per episode;
- consume the one final semantic attempt.

Outcomes are interpreted as follows:

- Model, embedding, Neo4j, or network disconnection: persist the checkpoint,
  revoke live authority, stop, and notify the operator. This is infrastructure
  interruption and does not consume the semantic envelope verdict.
- Transport succeeds but JSON decode, schema shape, or Pydantic correctness
  fails: persist the failure, revoke live authority, and classify the pinned
  `Graphiti 0.29.3 x Qwen3-32B-FP8 x vLLM 0.26.0` construction envelope as
  unsuitable for this frozen workload. Do not add another backend, parser,
  prompt, sampling change, repair step, or compatibility retry candidate.
- All four blocks complete: verify the run offline, publish formal
  `e1_breakdown.json`, revoke C2 authority, and advance only to C3/E2.

## Inherited limitations

Installed OpenAI SDK 2.53.0 has default internal `max_retries=2`. A C2
`llm-transport` span measures the complete SDK `.create()` latency but cannot
separate its physical HTTP retries. Pinned Graphiti separately uses Tenacity
with `stop_after_attempt(4)` for its retry predicate. These inherited behaviors
are recorded, not changed.

The thin shim observes transport attempts, successes, errors, token usage and
finish reason. JSON parsing occurs after the transport returns, and Pydantic
validation occurs at Graphiti call sites; legacy `parse_failure_count` and
`failure_events` must not be interpreted as C2 parser telemetry. Formal C2
error/retry accounting comes from the phase/transport span recorder.
