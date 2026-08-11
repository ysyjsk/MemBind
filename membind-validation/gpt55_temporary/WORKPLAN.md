# GPT-5.4-mini Bounded API Characterization Workplan

The directory name `gpt55_temporary/` is retained as a historical isolation
boundary. The active bounded experiment in this directory is pinned to the
relay alias `gpt-5.4-mini`; it does not silently inherit the active Codex model.
This remains a temporary diagnostic lane and does not advance V3/V4/V5/V6 or
any Native characterization authority state.

## Objective

Run exactly one frozen Native Graphiti construction episode with:

- construction LLM: `gpt-5.4-mini` through the active Codex provider's
  `/chat/completions` endpoint;
- embedding: local `BAAI/bge-m3` on CUDA device 0, which must identify as an
  RTX 3090 Ti;
- graph store: local, non-Docker Neo4j at a loopback Bolt/Neo4j URI;
- instrumentation: the already qualified C1 phase, LLM, embedding, and database
  wrappers;
- output: segmented, sanitized artifacts under `gpt55_temporary/artifacts/**`.

The result may characterize caller-observed API occupancy in one bounded
episode. It cannot establish a formal MemBind speedup, provider-side model
execution time, TTFT, ITL, or a publication-grade latency distribution.

## Frozen Inputs

```text
history_id             07741c45
source_sequence        0
episode_source_sha256  be983c489b10deea9c4d860f1e3203e4fa5d964154e004b814b2b5fee410156a
model                  gpt-5.4-mini
embedding              BAAI/bge-m3
embedding_revision     5617a9f61b028005a4858fdac845db406aefb181
embedding_dimension    1024
embedding_cache        /data/predator/ly/Mem/cache/huggingface/hub
remote_retry_count     0
planned_add_episode    1
```

`gpt-5.4-mini` is treated as a provider-specific alias. It is not presented as
an officially documented OpenAI public model identity. The alias must be sent
unchanged, and a returned model mismatch fails closed.

## Credential And Routing Contract

- Read the active provider, base URL, and bearer token only from
  `/home/ly/.codex/config.toml` after the outer preflight gate passes.
- Keep the bearer token in memory. Never place it in CLI arguments, artifacts,
  logs, exception messages, fingerprints, or source files.
- The provider config currently declares the Responses wire API. The user has
  explicitly authorized this isolated adapter to override only the wire path to
  `/chat/completions`; this is not described as config-native Chat support.
- Use direct OpenAI SDK transport with environment proxies and redirects
  disabled and SDK retry count set to zero. The explicit compatibility header
  is `User-Agent: OpenAI/Python 1.0.0`.
- Forward Graphiti-provided messages in their original role/content order. The
  adapter adds no system or developer message.
- The one structured Chat preflight uses one user message. The temporary Graphiti factory
  installs a fail-closed cross-encoder fence rather than
  authoring an additional reranker system prompt.

## TDD checkpoints And Execution Order

The frozen order is:

```text
RED unit contract
  -> GREEN focused test
  -> complete temporary offline regression
  -> immutable simple-judge artifact gate
  -> fresh structured Chat preflight
  -> preflight before dataset/GPU/Neo4j/Graphiti
  -> load the one frozen episode
  -> warm local BGE-M3
  -> connect local Neo4j
  -> assert the exact temporary namespace is empty
  -> construct Graphiti with raw episode storage disabled
  -> transfer resource ownership exactly once
  -> install C1 instrumentation
  -> exactly one add_episode
  -> restore instrumentation
  -> delete only the current tmp-api-char-* group
  -> close Neo4j and HTTP clients
  -> persist terminal checkpoint and analysis
```

Every exit path is checkpointed. A failed outer preflight must leave
`live_dependency_construction_started=false`. A failed structured preflight may
perform its one remote request but must still run before dataset/GPU/Neo4j/Graphiti.

Unit tests, run from `membind-validation/`:

```bash
.venv/bin/python -m unittest discover \
  -s gpt55_temporary/simple_judge/tests -v

.venv/bin/python -m unittest discover \
  -s gpt55_temporary/api_characterization/tests -v

.venv/bin/python -m unittest discover \
  -s gpt55_temporary/tests -v
```

## Stop Conditions

Stop before any local live dependency when any of these holds:

- simple Chat preflight is not one-request HTTP 200 success;
- returned model is not exactly `gpt-5.4-mini`;
- response does not finish with `stop`;
- preflight files are missing, symlinked, unreadable, or change during the gate;
- current provider/endpoint/model differs from the preflight manifest;
- fresh JSON-schema Chat preflight fails;
- current provider token or IP is forbidden by the gateway.

After local construction begins, fail and checkpoint on API attempt-cap
exhaustion, schema validation failure, unexpected cross-encoder invocation,
non-local Neo4j URI, non-RTX-3090-Ti CUDA device 0, dataset identity mismatch,
pre-existing state in the exact attempt namespace, malformed or out-of-root
trace spans, scoped cleanup failure, or resource-close failure. A successful
`add_episode` remains counted as completed even when a later cleanup/close step
fails; the episode outcome and terminal lifecycle outcome are recorded
separately.

## Current Execution State

Three mutually exclusive, one-request Chat paths and an authenticated `/models`
probe returned HTTP 403. These are gateway rejection observations, not model
latency samples. The local BGE-M3 preflight passed on an NVIDIA GeForce RTX
3090 Ti. The production CLI has been executed against the immutable failed
preflight and correctly stopped before config, dataset, GPU, Neo4j, or Graphiti
construction.

The failed attempt must not be reused as success evidence. Once gateway access
is repaired, create a fresh simple-judge attempt. Only its successful immutable
artifact may be supplied to:

```bash
.venv/bin/python -m gpt55_temporary.api_characterization.production_runner \
  --attempt-id <fresh-attempt-id> \
  --preflight-attempt-dir \
  gpt55_temporary/artifacts/simple_judge/<fresh-successful-attempt-id>
```

The implementation provider label is `local_bge_m3`. As a compatibility rule,
do not reuse partial v3_smoke_001 cache, prompt cache, embedding cache, trace,
or database state for any pass claim. This lane remains diagnostic even after
a successful episode.

The post-audit offline state is ready for that fresh attempt: simple judge is
19/19 GREEN, API characterization is 42/42 GREEN, the historical isolation
suite is 27/27 GREEN, and the full Native/mainline regression is 688/688 GREEN.
The real local Graphiti Neo4j driver also passed an empty-namespace read-only
query. No successful remote Chat response exists yet, so no construction
latency or bottleneck value may be inferred from the current artifacts.

## Evidence

- API latency interpretation: [API_LATENCY_METHODOLOGY.md](API_LATENCY_METHODOLOGY.md)
- Current run report:
  [artifacts/diagnostics/gpt54mini_bounded_001_report.md](artifacts/diagnostics/gpt54mini_bounded_001_report.md)
- Post-audit verification:
  `artifacts/diagnostics/gpt54mini_bounded_002_offline_verification.json`
- Current blocked checkpoint:
  `artifacts/api_characterization/gpt54mini-bounded-20260811-blocked-003/checkpoint.json`
