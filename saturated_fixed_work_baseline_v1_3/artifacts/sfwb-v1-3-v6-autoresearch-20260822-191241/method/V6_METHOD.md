# V6 Method: Certified Extraction Replay With Frontier-Aware Admission

## Resulting method

The V6 implementation is a narrow, fail-closed treatment of the measured
Graphiti native suffix.  It does not enlarge a source window, change vLLM
scheduling, or add a concurrency parameter.  Source-derived extraction work is
materialized ahead of publication through the shared `FrontierExecutor`, while
every non-certified LLM request goes through the single provider-level
`AdmissionArbiter`.  Native publication remains `Graphiti.add_episode()` in
strict source order.

During a native episode, only the frozen certified extraction callsites may use
an exact transcript.  The request identity includes source sequence, callsite,
ordinal, full messages, schema/model/sampling fields, client/transport identity,
and state-context digests.  A complete identity match consumes one immutable
transcript exactly once and does not enter the provider.  Any miss, changed
field, missing transcript, duplicate, or unconsumed item fails closed or falls
back to the real provider.  Non-certified native calls are never replayed.

The same `run_v6.py` executable supplies `matched-control` and `v6` policies.
Both use pinned Graphiti 0.29.3, the real `extract_nodes`/`extract_edges`
preparation path, real `Graphiti.add_episode()`, real construction and
embedding endpoints `8000/8001`, real Neo4j, the same frozen client/backend
configuration, instrumentation, lifecycle, and frontier/provider proofs.

## Why this mechanism

The L0 reducer found a 206.530 s preparation prefix, a 1,315.798 s native
occupied chain, only 0.187 s of inter-native gaps, and an exact 1,522.518 s V5
timer reconstruction.  Continuing to grow lookahead could not attack the
native ceiling.  The full campaign therefore tests only certified extraction
reuse plus frontier-aware overlap, with broad native request drift retained as
a negative result.

## Evidence and boundary

On full history `6071bd76` (46 sources), two counterbalanced pairs completed and
sealed.  The candidates consumed `92/92` certified transcripts in each arm
with zero duplicates/unconsumed items.  Candidate request comparisons were
`92` matches and `304`/`370` misses; misses remained real provider work.  Both
candidate arms had real future/native overlap (106 and 125 overlap pairs).
The machine-checked reducer is
`main/V6_MAIN_COMPARISON.json`; its `claim_status` is
`QUALIFICATION_ONLY`.

This is a complete end-to-end development-history qualification, not a final
semantic-equivalence or general performance claim.  The live graph/QA plane
remains `INVALID_RETAINED`; no quality or freshness claim is made.  Broader
claims require held-out histories and fresh same-time native B0/V5 controls.

## Accounting

`T_build` is the shared `FrontierExecutor` timer from `FORMAL_START` through
the final durable publication.  Provider transport attempts, usage, finish
reasons, replay/fallback work, queue/admission evidence, and preparation/native
overlap are reported separately.  Child phase totals and global vLLM counters
are never summed into request-level latency or treated as causal attribution.
