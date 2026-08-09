# V3 construction runtime evidence review

Generated: 2026-08-09

## Result

The restricted model-host interface is reachable through
`ssh zju-liuyi '<forced-command>'`. Only `status`, `list`, `tail`, and `read`
were used. Both accessed paths were under `/home/lhx/liuyi/logs`; no write,
ordinary shell, fallback transport, privilege expansion, or out-of-scope path
access was attempted.

The full construction startup log was read through the forced command and
hashed locally without persisting its raw body:

```text
remote relative path: logs/qwen3-32b-fp8-server.log
SHA256: 59633742b4a260682f08bc8f1838a9fcf6631d6ab582393a1686050c16e6eaac
```

Allowlisted evidence is persisted in:

```text
artifacts/environment/v3_construction_runtime_evidence_20260809.json
```

## Proven runtime configuration

The startup log proves:

- vLLM 0.26.0 with a V1 engine and V2 model runner;
- model root `/home/lhx/liuyi/models/Qwen3-32B-FP8`;
- served alias `qwen3-32b-fp8`;
- bfloat16 compute, FP8 quantization, and max sequence length 40960;
- `default_chat_template_kwargs.enable_thinking=false`;
- `StructuredOutputsConfig(backend='auto', disable_any_whitespace=False,
  disable_additional_properties=False, reasoning_parser='',
  reasoning_parser_plugin='', enable_in_reasoning=False)`.

The complete log snapshot contains startup and read-only metadata requests but
no generation request. Thus this is a restarted engine before any observed
structured generation. This is stronger than the earlier HTTP metadata, which
could not expose `structured_outputs_config`.

## Evidence boundary

The log proves the configured backend is `auto`; it does not expose the backend
that vLLM will select for the target request or the backend class eventually
held by the engine-level structured-output manager. Absence of generation is
bounded to the hashed snapshot and is not a guarantee against another client
after the snapshot.

The restart is a contract-preserving runtime-state reset, not proof that the
historical truncation has been corrected. It is sufficient to test exactly one
hypothesis: whether the unchanged complex Graphiti extraction request parses
when sent to the restarted auto-backend engine before a different observed
structured request initializes shared backend state.

## Gate transition

The immediate scope becomes:

```text
frozen_public_path_compatibility_probe_only
```

Exactly one existing compatibility probe may run with the frozen public
Graphiti request, model, schema, decoding values, seed, and `2048 -> 8192`
budget sequence. It must not call Neo4j or embedding. `v3_smoke_003 remains
forbidden` until that probe parses and its evidence is reviewed. V4/V5/V6 also
remain forbidden.

If the probe reproduces truncation, the blocker remains and no full smoke is
authorized. If it parses, persist the response/request hashes and review the
post-request restricted log before deciding whether a new V3 smoke is allowed.
