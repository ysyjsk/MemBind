# Retained Correctness Smoke History

This file indexes immutable evidence. It is not an execution queue. Current
work is controlled only by `MemBind_CURRENT_VALIDATION_PLAN_v1.2.md` and
`membind-validation/CURRENT_STATE.json`.

## Environment And Structured Output

- smoke01: M0 exceeded the 40960 context because Graphiti requested 16384
  completion tokens on top of a 24577+ token prompt. The shared 2048 clamp was
  added.
- smoke02: source 18 produced JSON truncated inside a string at 2048 tokens.
  The partial trace and cache remain retained.
- smoke03: DB instrumentation collided with a Graphiti Cypher parameter named
  `query`; a regression test fixed transparent parameter forwarding.
- smoke04: 2048, 4096, and 8192 attempts all exposed an unbounded
  `episode_indices` schema. The single-episode schema now freezes `[0]`.
- smoke05: a remote restart reduced context to 32768. A one-token usage probe
  proved the dynamic completion-budget path without truncating input.
- smoke06: source 19 used 32757 prompt tokens, proving a 32k service cannot run
  the frozen workload. `diagnostic_context_cap_005` retains the exact boundary.
- smoke07: after 40960 context and vLLM 0.26.0 were restored, M0 completed all
  46 episodes and captured 702 prompts. M2 then exposed U+2028 being treated as
  a JSONL separator; records now use ASCII LF framing.

## Correctness Candidate Presentation

- smoke08: M2 presented previous episodes newest-first. The fix preserves the
  native chronological selected window; every replay miss had zero live calls.
- smoke09: source 1 edge resolution used the same logical set in different
  physical order. `logical_content_ascending_after_top_k` now stabilizes prompt
  indices without changing membership.
- smoke10: M0 completed 46 episodes; M2 source 1 swapped `USER` and `italki`
  before candidate IDs. `logical_content_ascending_before_candidate_id` is now
  shared by all methods.
- smoke11: M0 completed 46 episodes; M2 source 5 shared nine of ten edge
  candidates but had a different RRF-cutoff member. Edge score inputs gained
  `logical_content_ascending_before_top_k`; Neo4j full-text internal cutoff
  remains a documented residual.
- smoke12: M0 completed source 0-8 before a model/runner infrastructure
  interruption. M2 never started; partial artifacts remain immutable.
- smoke13: M0 completed 46 episodes; M2 passed source 0-5 with zero live
  fallback, then source 6 lacked only `Sage Thrashers` among node candidates.
  `logical_node_content_ascending_before_top_k` is now shared.
- smoke14: M0 completed 46/46 and captured 702 prompt records. M2 passed source
  0-7 and failed at source 8 node dedupe with zero live fallback. Its prompt had
  one additional `SDG` candidate.

## Source-5/8 Forensics

- `diagnostic_smoke14_source8_M2_001` failed at source 7 edge resolution; the
  new fact matched M0 while one candidate entered and one left.
- `diagnostic_smoke14_source8_M0_002` failed earlier at source 5 with zero live
  LLM calls. The exact M0 prompt differed by one edge candidate.
- `diagnostic_smoke14_source5_M0_001` and `_002` both replayed six episodes
  successfully with zero live LLM calls and post-run node count zero. Each had
  18 entities and 25 edges before source 5. Two equal edge texts and five of
  seven compared query paths had different vector hashes, while full-text query
  hashes were stable.
- These forensic files persist logical fields plus vector SHA256, dimension,
  and norm. They deliberately do not contain raw vectors, so V1 cannot compute
  cross-run cosine/L2/max-abs from the old files alone.

No attempt listed here may be overwritten. A replacement always uses a new
attempt and run ID.
