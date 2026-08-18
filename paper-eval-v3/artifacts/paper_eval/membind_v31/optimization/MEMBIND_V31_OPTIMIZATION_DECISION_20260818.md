# MemBind v3.1 Optimization Decision (2026-08-18)

Status: `DIAGNOSTIC_ONLY` / `NON_MERGEABLE`

This note records a bounded, read-only analysis of the incomplete v3.1
attempt. It is not a methodology amendment and it is not an input to the
formal reducer. The frozen v3.1 plan, its execution envelope, and the failed
attempt remain unchanged.

## Evidence boundary

The source attempt is:

```text
attempt: membind-v31-smoke-20260818-004
block:   block-00
status:  FAILED_NON_REUSABLE
coverage: source prefix 0..30 published; source 31 terminal failure
```

The diagnostic input is the immutable trace:

```text
paper-eval-v3/artifacts/paper_eval/membind_v31/runs/
membind-v31-smoke-20260818-004/blocks/block-00/llm.jsonl
sha256: 2427035424d0fc2cbeecfcdcbda42b9a7a1c7fdff3954219c1e6078b9802d02a
```

The reproducible output is:

```text
paper-eval-v3/artifacts/paper_eval/membind_v31/optimization/diagnostics/
membind-v31-smoke-20260818-004-block-00/LLM_TRACE_DIAGNOSTIC.json
```

The trace contains complete transport lifecycle records for 502 requests:

| class | requests | prompt tokens | service-span sum |
| --- | ---: | ---: | ---: |
| Compile | 69 | 1,508,405 | 1,736.44 s |
| Frontier | 433 | 1,494,314 | 2,666.23 s |
| Total | 502 | 3,002,719 | 4,402.67 s |

The three distinct Compile fractions are therefore:

```text
rho_C_req     = 69 / 502                 = 13.745%
rho_C_prompt  = 1,508,405 / 3,002,719   = 50.235%
rho_C_service = 1,736.44 / 4,402.67     = 39.441%
```

The request and token fractions answer different questions. Request count
alone is not evidence that the State-Cut exposes little semantic work.
Service spans overlap: Compile interval-union is 1,458.74 s and Frontier
interval-union is 2,575.997 s. The analyzer records both the sum and the
union, and does not add overlapped intervals as wall-clock time.

## Scheduler diagnosis

With the observed transport intervals and `K=2`:

```text
observed max active                  = 2
active=1 fraction of service window  = 72.375%
active=2 fraction of service window  = 22.024%
under-capacity service time          = 2,736.918 s
```

The trace also permits a weaker transport-level calculation. The union of
intervals in which a submitted request had not started while active capacity
was available is 1.809 s, or approximately 0.048% of the 3,781.600 s service
window. This is **not** a ready-pool measurement: `llm.jsonl` does not record
legal Compile-ready artifacts, Prepared ROB occupancy, or the reason a
frontier was waiting. The only defensible conclusion is:

```text
READY_POOL_STARVATION_NOT_IDENTIFIABLE_FROM_LLM_TRACE
```

The data are consistent with `W=2` plus per-episode Compile ordering and a
serialized frontier Bind limiting the legal ready set. They do not establish
an admission-controller bug.

## Serving failure is a separate confound

The formal block did not terminate because of a scheduler invariant or a
context-admission HTTP 400. It received HTTP 200 responses followed by an
unterminated JSON parser failure at source 31. vLLM 0.26.0 defines
`FinishReason.LENGTH` as either consuming `max_tokens` or reaching
`max_model_len`; a structured-output grammar constrains sampled tokens but
does not extend the completion budget or synthesize missing closing tokens.
Qwen/vLLM reports also document structured generations consuming their cap
through whitespace or an unfinished structured response.

Relevant implementation evidence:

```text
https://github.com/vllm-project/vllm/blob/v0.26.0/vllm/v1/engine/__init__.py
https://github.com/vllm-project/vllm/blob/v0.26.0/vllm/config/structured_outputs.py
https://github.com/vllm-project/vllm/issues/17393
```

The current trace does not reliably retain `finish_reason` or actual
completion-token usage, so it cannot prove that this request ended with
`length`. The failure is therefore a serving/telemetry confound, not evidence
that MemBind is slower or faster. A future fresh attempt should record
`finish_reason`, completion tokens, requested cap, prompt tokens, and effective
structured-output backend. Changing the backend, completion cap, or server
context remains a separate deployment experiment; auto-closing or repairing
JSON is prohibited.

## Decision

The method should be optimized only in a separate lane, in this order:

1. Add offline-only fields/derivation for `ready_compile_count`, Prepared ROB
   occupancy, active Compile count, frontier phase, and explicit frontier wait
   reason. Missing fields must remain `NOT_OBSERVABLE`.
2. Use deterministic scheduler fixtures to compare `W=2`, `W=4`, and `W=8`
   without contacting services. Verify source-order publication, exact
   predecessor binding, ROB bounds, and artifact identity before measuring any
   performance proxy.
3. If the fixtures show legal ready work beyond `W=2`, test one bounded
   candidate (prefer `W=4`) in a fresh namespace, run id, and cache salt.
4. Within the legal ready set only, evaluate operator-cohort/prefix-affine
   ordering as a secondary locality optimization. Prompt, schema, semantic
   operator map, Bind order, and publication order must remain unchanged.

The following are explicitly deferred and must not be added to v3.1 as
patches:

```text
Snapshot Resolve / MVCC / OCC
Read-set validation
Selective semantic repair
Versioned resolution caches
Prompt changes intended to manufacture prefix overlap
Token-budget admission as a new correctness mechanism
```

Those mechanisms change the resolution semantics and require an independent
v4 correctness contract. They become justified only if the low-risk lane has
passed correctness gates yet the frontier/state-dependent suffix remains the
measured critical bottleneck.

## Acceptance gate for a future pilot

A candidate optimization is eligible for a bounded live pilot only after:

```text
focused TDD green
related and full offline regression green
fresh candidate manifest + namespace + cache salt
no changes to frozen v3.1 artifacts or old attempt
zero direct violations in the bounded run
source-order and serial-reference witnesses pass
no unexplained semantic-work reduction
```

The pilot result is diagnostic/non-mergeable until the frozen plan is amended
explicitly. A malformed or truncated provider response remains a failed
transport/semantic outcome; no JSON repair or post-hoc completion is allowed.
