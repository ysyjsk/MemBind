# V6 Proof Obligations (Qualification Draft)

The current code proves only the shared runtime and observation invariants:

1. `FrontierExecutor` publishes sources strictly in order and never advances
   the durable frontier on a failed preparation or native call.
2. Every real provider call goes through one `AdmissionArbiter`; certified
   replay calls are the only provider-free path.
3. Request observations retain source/call/ordinal identity and classify any
   changed field as a miss.
4. Shadow preparation is source-closed and does not call `Graphiti.add_episode`
   or publish to the authoritative namespace.

A final V6 semantic theorem is deliberately not claimed yet.  It requires a
   complete request-stability result for one native phase, a native-demand
   identity check at the actual Graphiti callsite, a miss/fallback trace, and a
   frozen-oracle differential test.  Under those assumptions, the proof is by
   induction over adaptive calls within an episode and then over ordered source
   publication.  Live stochastic runs can only be mapped to an allowed serial
   oracle trace; they cannot claim bitwise graph equality or preserved response
   distribution.

Crash safety remains a separate obligation: atomic durable frontier advancement,
idempotent completion, side-effect fencing, and rejection of partial captures
   must be demonstrated before any final method claim.
