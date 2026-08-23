# V6 Decision Cards

## Parrot (OSDI 2024)

Source: `https://www.usenix.org/conference/osdi24/presentation/lin-chaofan`

Observed symptom: V5's authoritative work is an adaptive Graphiti native suffix,
not a predeclared independent request graph.

Borrowed mechanism: application-level semantic variables and program/dataflow
frontiers can inform which work is safe to expose to a serving runtime.

Graphiti mismatch: the native request is generated from the current durable
graph state and prior responses.  A generic application scheduler cannot infer a
safe replay request from source order alone.

Decision: retain the existing `NATIVE_FRONTIER > FRONTIER_PREPARE >
FUTURE_PREPARE` provider admission order and require native Graphiti to generate
the demand before any replay.  Do not introduce a second scheduler or a fixed
lookahead parameter.

Next experiment: Probe A must compare complete request identities at the native
callsite for two sources; a phase is eligible only if all identity fields match.

## Sarathi-Serve (OSDI 2024)

Source: `https://www.usenix.org/conference/osdi24/presentation/agrawal`

Observed symptom: the V5 trace has long-tail native latency while future
preparation overlaps; endpoint-wide queue/running metrics are available but do
not attribute delay to a request.

Borrowed mechanism: separate prefill/decode interference as a measured serving
effect instead of treating client-side concurrency as a latency bound.

Frozen boundary: construction vLLM configuration, FCFS policy, prefix caching,
chunked prefill, and model settings are unchanged.  V6 cannot import a serving
policy change from this paper.

Decision: Probe B records only qualified queue/running/KV/prefix/preemption and
token counters.  TTFT/TPOT or server-side phase timing stay
`NOT_OBSERVABLE` unless a request-attributed qualification becomes available.

Next experiment: one foreground request with one real speculative request on
the same `8000` endpoint, counterbalanced AB/BA, after formal gate authorization.

## Speculate with Memory (arXiv:2607.12236)

Source: `https://arxiv.org/abs/2607.12236`

Observed symptom: replay/speculation is not novel by itself and can add shared
provider traffic.

Borrowed mechanism: lossless speculation requires a clear readiness contract
and an explicit fallback when the speculative result is not usable.

Graphiti-specific obligation: the request identity must include the complete
native messages/schema/client/provider identity plus the preceding graph-state
digest; native demand and publication remain authoritative.

Decision: V6's first arm is labelled `QUALIFICATION_ONLY` until request
stability and interference evidence identify one state-dependent native phase.
No quality claim is made while QA remains `INVALID_RETAINED`.

Next experiment: adversarial identity-diff tests followed by the smallest real
request-stability capture; broad drift switches the method to input/delta
reduction rather than a looser replay whitelist.
