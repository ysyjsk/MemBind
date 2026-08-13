# Judge/Evaluator Offline Implementation Evidence

Date: 2026-08-12

Scientific surface: `FUTURE_CONFIRMATION_INFRASTRUCTURE_ONLY`

## Scope And Safety

- Implemented only the offline Judge/Evaluator infrastructure.
- Real Judge requests performed: **NO**.
- Real construction-vLLM, embedding, Neo4j, Graphiti, and C5 requests performed by this task: **NO**.
- C5 live execution started: **NO**.
- `CURRENT_STATE.json` modified by this task: **NO**.
- C4 source, tests, process, namespace, checkpoint, and events modified or stopped by this task: **NO**. Changes under the active C4 run directory are independent background output from the already-running C4 process.
- `gpt55_temporary/**` inspected or used by this task: **NO**.

## Implementation Contract

The implementation adds a benchmark-native dispatch boundary:

```text
EvaluatorRegistry
    -> LongMemEvalAdapter
        -> official get_anscheck_prompt rubric
        -> JudgeBackend protocol
            -> OpenAICompatibleJudgeBackend
                -> Qwen3JudgeBackend
```

`EvaluatorRegistry` registers, retrieves, and asynchronously dispatches by an exact benchmark name. Unknown and duplicate evaluator names fail closed. It contains no prompt, model, Graphiti, Neo4j, C5, or dataset state.

`EvaluationItem`, `EvaluationResult`, and `JudgeQualificationRecord` are frozen schemas with explicit validation. `SUCCESS`, `INVALID_OUTPUT`, and `SERVICE_ERROR` remain distinct. `JudgeBackendResult` also rejects incoherent success/error states and negative retry counts.

`LongMemEvalAdapter` uses only the pinned official `get_anscheck_prompt` implementation. It covers the generic single-session/multi-session routes, temporal reasoning, knowledge update, preference, and abstention. The supplied hypothesis is treated as frozen system output; the adapter does not retrieve memory, regenerate an answer, construct a graph, aggregate C5 metrics, or exact-match the hypothesis.

`OpenAICompatibleJudgeBackend` uses dependency injection and `/v1/chat/completions`. `Qwen3JudgeBackend` freezes model `qwen3-32b-fp8`, temperature `0`, max tokens `10`, `n=1`, and no system message. Client-side thinking control sends `chat_template_kwargs.enable_thinking=false`; server-side mode sends no competing override and records the caller-asserted effective policy. SDK hidden retries are zero. The actual server-side policy remains intentionally unqualified offline, and `runtime_backend_config_hash` remains `null` until future online qualification.

The SDK client is pre-seeded with Linux platform identity in restricted tests because OpenAI SDK `2.53.0` otherwise performs a first-request worker-thread platform probe. The managed sandbox blocks cross-thread asyncio self-pipe wakeups. This is an environment adaptation only; it does not change prompt, response, or retry semantics.

## Parser And Failure Semantics

The headline parser preserves the pinned LongMemEval behavior:

```python
"yes" in raw_output.lower()
```

An independent audit parser accepts only unambiguous `YES` or `NO` forms. Empty, malformed, mixed yes/no, and substring-only values such as `yesterday` are `INVALID_OUTPUT`. The result preserves raw output, normalized output, official-compatible label, strict audit label, parse status, and parser disagreement. Consumers must aggregate only `status=SUCCESS`; no aggregation API was added in this offline-only scope.

Infrastructure failures alone may retry: timeout, connection/transport failure, HTTP 429, and HTTP 5xx. `NO` and malformed model output never trigger a retry. Exhaustion and nonretryable transport failures return `SERVICE_ERROR` with `label=None`. Only a stable exception class is persisted; exception text, credentials, authorization headers, endpoint userinfo, and private endpoint text are not persisted.

## Pinned Provenance

LongMemEval rubric source:

- Repository: `xiaowu0162/LongMemEval`
- Commit: `9e0b455f4ef0e2ab8f2e582289761153549043fc`
- Source: `src/evaluation/evaluate_qa.py`
- Git blob SHA-1: `4732f3772b04a2b9069121ade304e6320494abc2`
- Source SHA256: `ecce9c4c79dc89d99534ac17b383a5cbb5b9f0c69ee98adaf0684742e3d95251`
- Vendored scope: `get_anscheck_prompt` only
- Canonical function AST SHA256: `61836bc870cde12ca14cfae10d91f508eec3de6ed1f0d689fde37937083aa2a9`
- License: MIT; full notice is vendored and hash-bound.

TiMEM engineering reference:

- Repository: `TiMEM-AI/TiMEM`
- Commit: `6d279a5f5d40ee229e1995df15c182cb2062c71c`
- Source: `experiments/datasets/longmemeval_s/03_evaluation.py`
- Git blob SHA-1: `5cf4cd4c45a0c8cf1ba18dd50b4346516e15bfa9`
- Source SHA256: `11cf1a281fd217fc65ff9681ff64f7d55f61c5f7cbec3136f5a8a928de99233c`
- Role: engineering reference only; no TiMEM source was vendored.

The manifest binds rubric semantics to official LongMemEval, the adapter pattern to the TiMEM engineering reference, and the Judge backend to the local MemBind Qwen3 adapter. Local file hashes, the MIT notice, vendored-function AST, sanitized default request policy, and payload seal are checked against the validation root. Extra top-level code in the vendored rubric module is rejected. Manifest writes use canonical ASCII JSON and exclusive creation.

Verification level: the retained upstream snapshots independently verify source SHA256 and Git blob identity. The local MemBind Git object database does not contain the two upstream commit trees, so this artifact does not claim an offline proof of commit-to-path tree membership.

## Test-Driven Development

RED was established before each late hardening change:

1. A re-sealed manifest could be accepted without a local validation root, and a vendor file with extra top-level executable code passed the function-only AST check. Two targeted tests failed as expected.
2. `JudgeBackendResult` accepted five incoherent state combinations, including success without raw output, service error with raw output, and a negative retry count. The targeted schema test failed in all five subcases.

The minimum implementation changes then made the same targeted tests GREEN before the final focused suite ran.

Final focused command:

```bash
.venv/bin/python -m unittest -v \
  tests.test_evaluator_registry \
  tests.test_longmemeval_adapter \
  tests.test_qwen3_judge_backend
```

Result: `35 tests`, `OK`, `0 real external requests` by fake-client socket fail-fast and `httpx.MockTransport` wire-path tests.

## Regression Results

Judge focused gate:

- Result: **GREEN**
- Tests: `35/35`
- Artifact: `artifacts/tdd/judge_focused_green_20260812.log`
- SHA256: `cd2b7641ccfc42745a109b44c7dac8c290d333941200067f9bd46c4ee90285b8`

Broad Judge impact-closure gate:

- Result: **GREEN**
- Discovered: `947`
- Explicitly excluded known-baseline tests: `108` across the sandbox-limited C4 runner and nine protected live-pointer/historical-fixture modules
- Executed: `839/839`, `OK`
- Judge focused modules were included.
- Artifact: `artifacts/tdd/judge_offline_regression_green_20260812.log`
- SHA256: `3a1e46a7751b8d164f5098e50036d54c79c6e137de450dfed778befba43c6a77`

Whole-repository discovery was **NON-GREEN** and is not represented as a green artifact:

- Discovered: `947`
- Excluded only because of the demonstrated sandbox limitation: `13` tests in `test_native_characterization_c4_runner`
- Executed: `934`
- Result: `27 failures`
- Judge-related failures: `0`
- Failure modules: current/live-state contract tests, historical qualification/finalization fixtures, one reference-freeze provider-source hash, and old workplan/protocol pointer expectations.
- Artifact: `artifacts/tdd/judge_repository_full_regression_non_green_20260812.log`
- SHA256: `e31b2e488ed4c32e6c509a167ff0c8d7f59cb4199296ec125ea78e14d5ffa2be`

The 13 excluded C4 runner tests hit `SANDBOX_ASYNCIO_CROSS_THREAD_WAKEUP_LIMITATION`: background-thread `socketpair.send()` returns `EPERM`, so `asyncio.to_thread` completion callbacks cannot reliably wake the selector. A read-only heartbeat diagnostic completed the exact C4 grid (`10` blocks, `490` episodes), which distinguishes the sandbox limitation from a C4 algorithm deadlock or Judge regression. No C4 file was changed to obtain that diagnosis.

The requested filename `judge_full_regression_green_20260812.log` was intentionally not created because the whole repository is not green. The truthful artifacts are the broad impact-closure GREEN log and the whole-repository NON-GREEN log above.

## Dependency And Artifact Notes

- `httpx>=0.27` is declared directly because it is used by the transport contract and MockTransport tests.
- The active environment used `httpx 0.28.1` and `openai 2.53.0`.
- The repository's `uv.lock` is a pre-existing four-line placeholder and the venv contains no `uv` executable; this task did not claim or perform lock resolution.
- Raw Judge output is retained in each in-memory `EvaluationResult` for future secret-aware per-item persistence. No real per-item Judge artifact writer was exercised or claimed in this offline-only task.
- `network_evidence` in the manifest is a declared offline scope bound to the mock-only test evidence above; it is not treated as a self-authenticating proof of absence.

## Canonical Manifest

- Artifact: `artifacts/protocol/judge_upstream_manifest_20260812.json`
- File SHA256: `ec1062f4adc7e5a852fd38082f0ddc5f7c92c3fc32d3bf2c7cfb5c2117c4c7ce`
- Payload SHA256: `2d2a1511c37b6aa4cf3b27c3ce9f8eba7b762384e7a23b490e03032da3f5b7a2`
- Canonical validation against the current validation root: **PASS**

## Residual Boundaries

- `INVALID_OUTPUT` retains the official-compatible boolean label for benchmark fidelity, so downstream code must filter on `status=SUCCESS` before aggregation.
- Server-side thinking control remains a declared deployment policy until future live qualification binds a runtime backend config hash.
- Exclusive manifest creation prevents overwriting existing evidence, but it is not a crash-atomic no-replace publication protocol.
- `EvaluationResult` is frozen, while its metadata dictionary remains mutable; future durable result serialization should copy and seal metadata.

No online Judge qualification, human audit, held-out confirmation evaluation, Cohen's kappa, confusion matrix, C5 metric, or C5 authorization was performed.
