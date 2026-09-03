# Structured Output Design Decision

Status: `DESIGN_SELECTED_BEFORE_LIVE_RUN` (2026-09-03)

## Evidence Reviewed

The installed, pinned stack is Graphiti `0.29.3`, vLLM `0.26.0`, and XGrammar
`0.2.3` in `membind-local`.  The relevant local source is:

- `graphiti_core/utils/maintenance/edge_operations.py`: `extract_edges()` asks
  for one `ExtractedEdges` object whose `edges` list has no finite cardinality
  in the upstream model, and uses `max_tokens=16384`.
- `graphiti_core/prompts/extract_edges.py`: edge facts are binary between two
  distinct listed entities, but the prompt does not define a finite exhaustive
  search space.
- `vllm/v1/structured_output/backend_xgrammar.py`: JSON Schema is compiled into
  a grammar and token masks are applied during decoding.  Grammar validity is a
  syntax/property guarantee, not a proof that a semantic relation set is
  complete.
- `xgrammar` `0.2.3`: the JSON grammar compiler has no knowledge of Graphiti
  evidence provenance or of the set of facts that an LLM should have found.

The previous live failures are retained under the local experiment roots.  In
particular, p4/p5 repeatedly returned a previous edge after the rolling cursor
had advanced.  The old implementation then requested a terminal-only schema;
that makes a model assert `no_additional_edge` without an independently
checkable proof and is not an acceptable success path.

## Candidate Comparison

| Design | Semantic completeness | Finite request bound | Small-model compatibility | Provider calls | Graphiti semantics / risk |
| --- | --- | --- | --- | --- | --- |
| A. Generative cumulative cursor/paging | Not provable; provider can repeat or stop early | No: cumulative continuation grows with pages | Poor after long history | Data-dependent and unbounded | Closest prompt shape, but the observed failure is structural |
| B. Evidence-window extraction | Covers source text if windows are lossless and overlap is audited | Yes per task, but an edge list can still saturate | Good | O(number of windows) | Preserves multi-entity reasoning, but needs an overflow failure rule |
| C. One task per entity pair | Complete over the declared pair domain; omitted nodes remain outside the claim | Yes; one bounded response per pair | Good | O(n^2) | Strongest coverage proof, but call count is too high for large domains |
| D. Finite pair-task batches (selected) | Complete over every declared pair in every lossless evidence window; missing/overflow pairs fail closed | Yes: task list, pair list, and response cardinality are fixed before calls | Better than A; each response is small | O(n^2 / batch_size), with a preflight task-count guard | Shared compatibility substrate; output is merged back to the unchanged Graphiti edge type |
| E. Retrieval/scored candidate sampling | Not complete and can silently drop facts | Usually bounded | Good | Low | Changes semantic algorithm and is disallowed for this experiment |

The selected design is a finite pair-task plan.  Entity names are taken from
the completed node extraction provenance.  Evidence is split into contiguous,
role-preserving windows; adjacent windows are retained for boundary coverage.
For each window, all unordered distinct entity pairs are enumerated in a
canonical order and packed into fixed-size tasks.  Every task carries its
declared pair IDs, and the response must acknowledge every pair, including
empty relation lists.  A response that reaches the per-pair relation cap,
omits a pair, contains an unlisted endpoint, repeats a pair, or is malformed is
`FAIL_CLOSED`; it is never treated as an empty result.  Relations in both
directions are allowed within the declared pair.

The selected packing is one pair per task with at most two relations per pair.
That keeps the worst-case finite response below the pinned 16,384-token edge
budget while allowing distinct facts for one pair; a third relation fails
closed. The preflight task guard is 1,024 tasks per source, so the full task
count remains explicit before any provider call.

This avoids an unbounded cursor and avoids an unbounded provider-call stream.
The logical pair domain is still quadratic in the number of entities, which is
the honest cost of complete arbitrary binary relation coverage.  It is made
explicit and bounded per source by the preflight task-count certificate and by
keeping each pair's response small. The certificate reports the
exact task count and rejects a source whose declared domain exceeds the frozen
finite task budget; it never samples or truncates the tail.

## Required Answers

1. XGrammar constrains JSON syntax, field domains, and cardinalities.  It cannot
   prove that the model found every fact because semantic completeness is not a
   property of a context-free output grammar.
2. A source search space is the Cartesian product of its losslessly windowed
   evidence and the canonical unordered pairs of entities actually emitted by
   node extraction.  That is the declared domain, not a claim about omitted
   entities.
3. The provider-call count is computable before execution: node calls plus the
   number of finite pair tasks (plus any separately declared timestamp calls).
   No cursor loop is allowed to add calls after the plan is sealed.
4. Pair batching amortizes request overhead.  A quadratic logical domain is
   reported rather than hidden; a finite task-count guard prevents an
   accidental large-source explosion and causes an auditable failure.
5. Windows are contiguous and role-preserving.  Adjacent-window tasks include
   the boundary text, so a cross-boundary fact is represented in at least one
   declared task.  The window manifest and source hashes are part of coverage.
6. Multiple relations for a pair are represented in the bounded list.  If the
   list reaches its frozen cap, the task fails closed as potentially truncated;
   it is never silently clipped and never “proved” by a terminal-only answer.
7. Native, Async, and V6.1 receive the same window text, pair-task set, schema,
   model, decoding settings, and merge/dedupe rule.  Only execution order and
   scheduler admission differ; the shared substrate identity is recorded in
   every contract.
8. Windowing, pair-task packing, schema validation, and deterministic merge are
   compatibility substrate changes shared by all arms.  Changing candidate
   membership, Graphiti prompts, state update order, or publication semantics
   would be an algorithm change and is outside this experiment.

## Research Context

- PURE (Zhong and Chen, NAACL 2021, “A Frustratingly Easy Approach for Entity
  and Relation Extraction”, <https://arxiv.org/abs/2010.12812>) separates entity
  and relation decisions over explicit spans.  It motivates
  explicit candidate domains, but does not prove LLM semantic completeness and
  does not justify sampling here.
- Ray (Moritz et al., OSDI 2018,
  <https://www.usenix.org/conference/osdi18/presentation/moritz>) models work
  as an explicit task graph.  We adopt the sealed task ledger and dependency
  accounting, not Ray's distributed runtime or scheduling claims.
- Orca (Yu et al., OSDI 2022,
  <https://www.usenix.org/conference/osdi22/presentation/yu>) motivates
  iteration-level scheduling and explains why request size and interference
  must be measured.  It does not make an unordered Graphiti extraction
  complete.
- SGLang (Zheng et al., 2024, <https://arxiv.org/abs/2312.07104>) shows that
  structured programs can coordinate multiple model calls.  We use the narrow
  idea of an explicit finite call graph; we do not import SGLang's runtime or
  treat grammar execution as a semantic oracle.

## Claim Boundary

The implementation can claim only: bounded physical requests, explicit finite
task coverage for the declared node/evidence domain, fail-closed overflow, and
shared logical work across arms.  It cannot claim upstream byte identity,
semantic recall for entities that node extraction omitted, or quality/performance
benefit before fresh exact-failure reproduction, complete-history qualification,
and the 45-cell formal campaign pass.
