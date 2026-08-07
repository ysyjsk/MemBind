# MemBind Basic Validation: v1.1 Optimized Execution Plan

This is the execution overlay for MemBind_basic_validation_experiment.md. It
freezes the deployment topology requested for this run and makes TDD a hard
gate before any live experiment.

## Topology

Machine A provides both OpenAI-compatible model services:

- construction: http://10.87.5.247:8000/v1/, model qwen3-32b-fp8;
- embedding: http://10.87.5.247:8001/v1, model qwen3-embedding-0.6b;
- both services use the same API key from the untracked project .env file.

Machine B runs Graphiti, the replay driver, and Neo4j Community 5.26 locally
without Docker. All elapsed times are measured on Machine B with
time.monotonic_ns(). M0, M1, and M2 use the same remote endpoints, model
parameters, API key, network path, and concurrency cap.

## TDD Gates

1. Red gate: add or update the invariant test before implementation.
2. Green gate: run all tests with .venv/bin/python -m unittest discover -s tests.
3. Contract gate: call both /v1/models endpoints, run 20 consecutive structured
   construction requests, and make one embedding request; require 20/20 valid JSON,
   the expected model ids, and embedding dimension 1024.
4. Integration gate: start local Neo4j, build indexes, clear the database,
   ingest one warm-up episode, search it, clear it again, and repeat the
   isolation check.
5. Smoke gate: run one calibration instance through M0, M1, and M2; compare
   canonical graph and retrieval outputs before formal runs.
6. Characterization gate: after correctness smoke passes, implement tracing,
   network/cache lifecycle, and statistics through red/green tests; then pass
   overhead, cache-reset, network-baseline, and normalization guardrails.
7. Formal gate: freeze DELTA_MS, phase map, fairness policies, and the 64-run
   plan. Performance runs use balanced `(question_id, repeat)` method blocks.
   A failed run is retained and never silently replaced.

## Structured-output budget

The frozen request budget remains 2048 completion tokens. Graphiti 0.29.3 asks
for 16384 tokens for edge extraction, so every initial request is clamped to
2048. A long edge list can nevertheless end mid-JSON at that cap. When parsing
the constrained JSON fails, every method uses the same single bounded retry at
8192 tokens. Both attempts count toward call/token metrics. This is an explicit
protocol deviation; it is preferable to accepting malformed JSON or silently
dropping extracted facts.

Every experiment call extracts exactly one current episode. Graphiti's field
description therefore requires `episode_indices=[0]`, but its generated schema
leaves that integer array unbounded. Qwen/vLLM was observed to emit
`0,1,2,...` until the completion budget was exhausted. The shared schema used
by M0, M1, M2, and the prompt-cache hash now freezes this field to one item with
value 0. Prompts and parsed facts are otherwise unchanged.

The construction service previously exposed a 32768-token context window. If
vLLM rejects a request because the declared completion budget crosses that
window, a one-token response probe obtains the exact prompt-token usage from a
successful response. The same complete prompt is then retried with
`context_limit - exact_prompt_tokens - 32`. No input text is truncated, and
both the probe and actual reduced budget are recorded in call instrumentation.

The full `smoke06` run proved that this compatibility path is not sufficient
for every frozen episode. Source sequence 19 produced a 32757-token native
Graphiti node-resolution prompt, leaving only 11 tokens before safety margin
under the restarted 32768-token service. The retained
`diagnostic_context_cap_005` run records a minimum context of 34837 for the
primary 2048 completion budget and 40981 for the full 8192 overflow budget.
The minimum runtime contract therefore remains `max_model_len >= 40960`.
On 2026-08-07 the user restored that context limit and explicitly approved
vLLM 0.26.0 in place of the original 0.23.0 protocol version. Version 0.26.0
is now frozen uniformly for M0, M1, and M2. Input truncation and prompt
rewriting remain prohibited workarounds.

## Deterministic prompt candidate presentation

Graphiti's edge RRF search retains responsibility for selecting the top-K
candidate set. Its Neo4j BM25 and cosine source queries now use
`logical_content_ascending_before_top_k`: immediately before their outer
database cutoff, equal scores are ordered by fact, relation, temporal fields,
and endpoint names without UUIDs. This stabilizes the ranked inputs consumed by
RRF. Neo4j's full-text procedure applies its own internal limit before that
outer ordering, so an exact tie at the procedure boundary remains a documented
residual risk to be tested by the next correctness smoke.

Graphiti's node-dedup maintenance path uses direct cosine searches with a
15-candidate cutoff. Those Neo4j queries use
`logical_node_content_ascending_before_top_k`: equal scores are ordered by
UUID-independent name, summary, and labels before `LIMIT`. The rule stabilizes
candidate membership before the later prompt-level canonical presentation.

The original implementation then assigned prompt indices in the physical
result order, which is not stable across fresh graphs because UUIDs and Neo4j
equal-score order are not protocol fields. All three methods also use
`logical_content_ascending_after_top_k`: after top-K selection, the selected
edges are ordered by logical fact/relation/temporal content before prompt
indices are assigned, and each reranker score moves with its edge. Candidate
membership and search cutoffs are unchanged. This shared normalization is
active in correctness and performance lanes.

Node-resolution candidates use the companion contract
`logical_content_ascending_before_candidate_id`. Graphiti still selects and
deduplicates the candidate set; logical nodes are ordered by the prompt-visible
name, labels, summary, and attributes before IDs are assigned. The prompt and
the `candidate_id -> node` mapping share that same ordered list.

A read-only miss remains a hard correctness failure and never calls the live
model. The run additionally persists the requested PromptParts, per-component
hashes, prompt name, and nearest cache record under
`artifacts/unexpected_prompts/`; API keys are never part of those records.

## Formal Lanes

Correctness lane is eight M0 capture runs followed by eight M2 read-only replay
runs. A prompt cache miss in replay is an immediate failure. Performance lane
is 8 instances x 3 methods x 2 repeats, with application response cache
disabled. The old global shuffle is replaced by balanced method blocks keyed
by `(question_id, repeat)` with seed 20260806.

## v1.1 Characterization and fairness gate

No characterization or formal run starts before correctness smoke parity. The
post-smoke sequence is fixed: instrumentation contract tests; in-memory spans
and interval-union analysis; request/DB/pipeline tracing; overhead gate; live
prefix/embedding cache-reset contract; 100-probe network baseline; upstream vs
deterministic M0 guardrail; four M0 calibration instances; C1/C2/C4/C8
sensitivity; rho 0.5/1.0/1.5 plus fixed-seed Poisson sensitivity; freeze; then
the blocked 64-run formal plan.

Each measured run uses hot engines with cold cross-run prefix, embedding, and
logical graph state. Pre/post network gates run outside measurement, vLLM must
be idle, and telemetry spans the measured run through drain-to-zero. Explicit
infrastructure failure reruns the entire three-method block under a new block
ID; treatment-induced overload remains a method result.

## Local Neo4j

Use scripts/install_local_neo4j.sh and scripts/start_local_neo4j.sh. The
distribution is neo4j-community-5.26.0, listening on 127.0.0.1:7474 and
127.0.0.1:7687. NEO4J_PASSWORD is read from .env and the database is cleared
and rebuilt before every run.

## Evidence and artifacts

The frozen split, source SHA, environment manifest, service contract results,
traces, canonical graphs, retrieval metrics, and statistical summary are
written under artifacts/. Secrets are never copied to artifacts or logs.

## Retained smoke failures

- `smoke01`: M0 failed when Graphiti requested 16384 output tokens with at least
  24577 input tokens, exceeding the remote model's 40960-token context. The
  request clamp to 2048 fixed that context-budget failure.
- `smoke02`: M0 reached source episode 18, then the 2048-token edge extraction
  ended inside a JSON string. The run, partial trace, and partial prompt cache
  remain under `artifacts/`; a 4096 retry was then shown by `smoke04` and the
  cached diagnostic replay to end with `finish_reason=length` as well. The
  evidence-driven overflow limit for the next attempt is 8192.
- `smoke03`: the first DB-query instrumentation wrapper conflicted with a
  Graphiti Cypher parameter named `query`; a red test reproduced it and the
  wrapper now preserves arbitrary query parameters.
- `smoke04`: source episode 12 exposed the unbounded `episode_indices` schema.
  The retained diagnostic records prove that 2048, 4096, and 8192 all ended
  with `finish_reason=length`; constraining the single valid index to `[0]`
  made the same cached 12-episode replay complete with zero parse failures.
- `smoke05`: after the remote restart reduced context from 40960 to 32768,
  source episode 16 exceeded the window only because of its declared output
  budget. A retained diagnostic replay proved that a one-token probe measured
  31454 prompt tokens and the final 1282-token cap completed in 419 tokens.
- `smoke06`: M0 successfully passed the earlier source 15/17 context cases but
  failed at source 19 because the complete prompt itself used 32757 of 32768
  tokens. `diagnostic_context_cap_005` reproduced the boundary from the
  retained partial cache and persisted the exact context requirements. The
  restarted endpoint also reported vLLM 0.26.0 rather than the then-frozen
  0.23.0. The user subsequently approved 0.26.0 and restored 40960 context;
  `artifacts/environment/construction_context_blocker.json` retains the failure
  evidence and is resolved only by a successful live environment gate.
- `smoke07`: after the 40960/0.26.0 runtime gate passed, the full M0 capture
  succeeded for all 46 episodes and produced the retained 702-record cache.
  Its first M2 load exposed JSONL handling of U+2028; records are now delimited
  only by ASCII LF and the original attempt remains retained.
- `smoke08`: read-only replay loaded the complete cache and correctly blocked
  every live fallback. Sources 2-45 exposed that M2 presented previous episodes
  newest-first while native Graphiti presents the selected recent window
  chronologically. The red tests and fix preserve native selection and order.
- `smoke09`: replay advanced through extraction and failed at source 1 edge
  resolution. The candidate facts were identical to M0, but their physical
  Neo4j/RRF presentation order and indices differed. This evidence motivated
  the shared `logical_content_ascending_after_top_k` contract; the failed run
  and trace remain immutable.
- `smoke10`: M0 capture completed all 46 episodes; M2 replay failed at source 1
  node resolution. Its seven semantic candidates matched M0, but `USER` and
  `italki` were reversed before `candidate_id` assignment. The red regression
  test motivated `logical_content_ascending_before_candidate_id`; all smoke10
  artifacts remain immutable.
- `smoke11`: M0 again completed 46 episodes and persisted 702 prompt records;
  M2 failed read-only replay at source 5. Offline comparison found nine common
  edge candidates in identical presentation order but a different tenth
  member at the RRF cutoff. The red query-rewrite tests motivated
  `logical_content_ascending_before_top_k`; the procedure-internal cutoff risk
  remains explicit, and all smoke11 artifacts remain immutable.
- `smoke12`: M0 completed sources 0-8 and persisted 144 prompt records before
  a construction-model/runner infrastructure interruption. M2 never started;
  the partial run, trace, cache, and explicit interruption summary are retained.
- `smoke13`: after the recovered environment and Neo4j gates passed, M0
  completed all 46 episodes and persisted 686 prompt records. M2 replay passed
  sources 0-5, then failed at source 6 `dedupe_nodes.nodes` with zero live
  fallback calls. The M0 prompt had 26 existing candidates while M2 had 25;
  `Sage Thrashers` was the only missing logical candidate and all other prompt
  content was byte-identical after normalizing that block. This evidence
  motivated `logical_node_content_ascending_before_top_k`; all smoke13
  artifacts remain immutable.

No failed attempt is overwritten. A replacement uses a new attempt/run id.
