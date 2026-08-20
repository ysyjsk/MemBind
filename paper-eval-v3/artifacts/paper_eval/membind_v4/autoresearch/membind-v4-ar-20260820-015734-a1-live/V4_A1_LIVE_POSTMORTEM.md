# V4 A1 Live Postmortem

- Candidate: `c01`
- Protocol: `A1`
- History: `07741c45`
- Source count: `20`
- Run root: `membind-v4-ar-20260820-015734-a1-live`
- tmux session: `membind-v4-a1-c01-20260820`

## Service And Identity

The construction vLLM endpoint on port `8000` returned HTTP 200 with model
`qwen3-32b-fp8`. The embedding endpoint on port `8001` returned HTTP 200 with
model `qwen3-embedding-0.6b`. Neo4j HTTP and Bolt probes were ready. The A1
sidecars verified against history `07741c45`, the sealed source inventory, and
the sealed arrival trace.

## Runtime Outcome

The live block reached `completed_source_prefix=19` with all 20 source states
`PUBLICATION_DURABLE` and 120 durable lifecycle events. Its block-level raw
diagnostics were `makespan_ns=1296939342592` and
`p95_freshness_ns=615073560160`. These values are diagnostic only.

The candidate failed closed during post-run speculative LLM trace alignment:
`speculative_llm_trace_alignment_failed`. The failure is recorded in
`candidates/c01/failure.json` as `FAILED_NON_MERGEABLE`; no candidate summary or
reduction was produced. The raw block timing is therefore not eligible for a
candidate gate, freeze, or formal main table.

## Root Cause And Fix

The adapter emitted the exact state-bound token HMAC for a semantic MISS while
the public provider trace contained the speculative request HMAC. The reducer
correctly refused to infer a match. The v4 adapter now records both identities,
and metric derivation aligns MISS token waste with the speculative HMAC while
still validating the exact HMAC independently. A regression test covers
different exact/speculative HMACs.

Verification after the fix: v4 focused suite `166 passed`; full paper-eval
suite `2470 passed, 1 warning`; compileall and `git diff --check` passed.

## Decision

This one-shot A1 execution is `STOP`, not `FREEZE`. The original live artifact
remains immutable and must not be rewritten. No `c02` tuning and no formal
four-history run are authorized from this evidence. A future live attempt
requires an explicit new protocol authorization after the corrected adapter
has been reviewed.
