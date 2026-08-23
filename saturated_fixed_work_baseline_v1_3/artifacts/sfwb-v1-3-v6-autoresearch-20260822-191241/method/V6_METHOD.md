# V6 Method (Qualification Draft)

## Current conclusion

The sealed V5 trace places 86% of build time in the ordered native suffix.
Future preparation is already mostly hidden: the reducer reports a 206.530 s
source-0 preparation prefix, a 1,315.798 s native occupied chain, only 0.187 s
of inter-native idle gaps, and an exact 1,522.518 s timer reconstruction.

Therefore V6 does not enlarge a source window or add a new concurrency knob.
The active hypothesis is one narrow, state-dependent native request phase,
currently `attributes-summary`, subject to Probe A request identity evidence.

## Executable arms

`run_v6.py` is the single executable for both `matched-control` and `v6` policy
arms.  Both use the frozen Graphiti 0.29.3 native `Graphiti.add_episode()` path,
the same `8000/8001` endpoints and client configuration, the shared provider
arbiter, ordered durable frontier, and the same instrumentation/lifecycle.

The current `v6` arm is intentionally a request-stability qualification arm:
source-closed extraction shadow work is observed and existing certified
extraction calls are bound.  It is not a final performance treatment and its
seal is `claim_status=QUALIFICATION_ONLY`.

## Eligibility and fallback

The public request observation contains only digests.  A private 0600 capture
may contain complete messages for offline diagnosis.  Any changed identity
field is a miss.  Native Graphiti still decides demand/control flow and remains
the only publication path.  A future selected phase must use complete identity
matching or a separately proved read certificate; no whitelist is permitted.

## Performance accounting

The timer is the shared FrontierExecutor `FORMAL_START` to final durable
publication boundary.  Phase span totals are attribution-only because child
spans overlap.  Replay opportunity and online speculation/interference must be
reported separately; endpoint-global counters cannot be turned into
request-level latency attribution.

QA remains `INVALID_RETAINED`; this draft makes no quality or freshness claim.
