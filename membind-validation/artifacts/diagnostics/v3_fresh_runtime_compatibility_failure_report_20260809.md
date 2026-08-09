# V3 fresh-runtime compatibility failure report

Generated: 2026-08-09

## Result

The single authorized frozen public-path compatibility probe completed against
the restarted vLLM 0.26.0 construction service. It failed with:

```text
classification: exact_historical_truncation_reproduced
error_type: JSONDecodeError
prompt tokens: 5795 (matches retained history)
high-level attempts: 4
completion calls: 8
parsed: false
```

This is a retained V3 failure, not a service interruption. The current blocker
remains `v3_smoke_002_m0_structured_output_failure`;
`v3_smoke_003 remains forbidden`.

## Immutable probe evidence

```text
artifact: artifacts/environment/v3_actual_schema_compatibility_probe_20260809_005_fresh_restart.json
SHA256: fd1b23026689008ce9a5976581b519c2a7d62fc5c2ea05eb0964f5387e10a041
checked_at: 2026-08-09T03:40:20.032631+00:00
```

The probe used the installed Graphiti public `generate_response` path for
question `c6853660`, source sequence 1. It retained the frozen Qwen model,
Graphiti prompt, constrained `ExtractedEntities` schema, temperature 0, top-p
1, seed 20260806, retry count, and `2048 -> 8192` budgets. It made
no database or embedding call and persisted no response body, prompt body,
credential, or Authorization header.

## Exact observed trajectory

All four high-level attempts produced the same pair:

| budget | finish | completion tokens | body length | body SHA256 | repetitions |
|---:|---|---:|---:|---|---:|
| 2048 | `length` | 2048 | 5386 | `d9340f0bc347bbb5d7049aa55bee2739485755444f28e216e523c4bd3a5b0a16` | 4 |
| 8192 | `length` | 8192 | 21456 | `94fb64c3921b3e1e7bfecee99e6faa00e620fea7f949cc0d839d8b185035aef0` | 4 |

The 8/8 responses are bitwise identical to the corresponding retained
historical failures. The artifact records 8 structured parse failures, 4
high-level structured response failures, and 3 outer retries. The request
envelopes also repeat exactly within each budget.

## Restricted post-request evidence

Only the forced-command interface was used:

```text
ssh alias: zju-liuyi
remote scope: /home/lhx/liuyi/**
commands: tail logs/qwen3-32b-fp8-server.log; read logs/qwen3-32b-fp8-server.log
remote full-log SHA256: d71df1614b6da7d4d9549d739d1f2c0d67351916aa88ef5be0cc7aa2c818a761
chat completion POST: 8
HTTP 200: 8
server error markers: 0
```

Sanitized post-request evidence is in
`artifacts/environment/v3_post_compatibility_runtime_evidence_20260809.json`.
The raw remote log was not persisted locally, and no remote write was
performed.

The HTTP evidence distinguishes this result from a connectivity or service
availability failure. It does not prove which backend `auto` selected for the
request, and it does not identify vLLM guided decoding, Qwen behavior, or
another request/runtime interaction as the root cause.

## TDD and gate decision

Before the state transition, focused contracts were made red in:

```text
artifacts/tdd/v3_fresh_runtime_failure_contract_red_146.log
SHA256: a1de4a9b691520d1688eea5a6e0e78dec42f94dd61e8a46e4de8c47167a4435b
```

The same TDD change also requires an existing compatibility output to fail
before any model call, preventing accidental reuse of this consumed one-shot
attempt ID.

The focused TDD chain is:

| phase | artifact | SHA256 | outcome |
|---|---|---|---|
| behavior/state red | `artifacts/tdd/v3_fresh_runtime_failure_contract_red_146.log` | `a1de4a9b691520d1688eea5a6e0e78dec42f94dd61e8a46e4de8c47167a4435b` | 2 expected errors |
| minimal behavior/state green | `artifacts/tdd/v3_fresh_runtime_failure_contract_green_148.log` | `d52e2e5b2c922352176e37be33084013232887ae54dad9ff6fa10158cb2b7162` | 2 passed |
| expanded focused green | `artifacts/tdd/v3_fresh_runtime_failure_focused_green_151.log` | `9bda8df9d1fe39d8a40654215af5752d56b6ab11f53e3edf1664032359815551` | 40 passed |
| state-evidence red | `artifacts/tdd/v3_fresh_runtime_failure_state_evidence_red_152.log` | `275eda4628ba7c0b1cd36e2866ded6b312884d444302329634e20ba8be332618` | 1 expected error |
| state-evidence green | `artifacts/tdd/v3_fresh_runtime_failure_state_evidence_green_153.log` | `7425f1bd4d3d104cf7fd4ae175fb2f87e86399ac598017399170683cdcc41154` | 1 passed |
| full mainline regression | `artifacts/tdd/v3_fresh_runtime_failure_full_regression_green_154.log` | `09d6f2b4867496291e116e6e6f8358eb606d7c05648be20b189cff09780ed31d` | 258 passed |
| full-evidence red | `artifacts/tdd/v3_fresh_runtime_failure_full_evidence_red_155.log` | `3edc6c3a008193f73276a6a7c0b020c9d74a17fd184629151480cb70c7b31b06` | 1 expected error |
| full-evidence green | `artifacts/tdd/v3_fresh_runtime_failure_full_evidence_green_156.log` | `7425f1bd4d3d104cf7fd4ae175fb2f87e86399ac598017399170683cdcc41154` | 1 passed |

The full regression log replaces elapsed time, generated timestamps, and
temporary-directory names with stable placeholders. It used
`.venv/bin/python -m unittest discover -s tests -q`; it did not call a model,
embedding endpoint, or Neo4j and did not discover the quarantined temporary
lane.

The gate is now:

```text
current_action_scope: blocked_waiting_for_explicit_protocol_deviation
v3_smoke_003_authorized: false
forbidden_until_pass: V4/V5/V6/future_work
```

No further live probe, full smoke, M1/M2 run, calibration, profiling, or formal
evaluation is authorized. Progress requires an explicitly approved protocol
deviation or a service-side correction supported by new evidence. A remote
service change would additionally require explicit write capability in the
restricted SSH interface; it must not be bypassed.
