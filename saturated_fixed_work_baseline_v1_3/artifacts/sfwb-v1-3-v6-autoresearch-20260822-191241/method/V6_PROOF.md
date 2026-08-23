# V6 Proof Obligations And Result

The implementation and sealed full-history evidence prove the following
runtime invariants:

1. `FrontierExecutor` publishes sources strictly in order and never advances
   the durable frontier on a failed preparation or native call.
2. Every real provider call goes through one `AdmissionArbiter`; certified
   replay calls are the only provider-free path.
3. Request observations retain source/call/ordinal identity and classify any
   changed field as a miss.
4. Shadow preparation is source-closed and does not call `Graphiti.add_episode`
   or publish to the authoritative namespace.
5. The full-history frontier is durable and ordered through source `45` in all
   four arms; provider max outstanding is `8`, future max outstanding is `7`.
6. Both candidate arms have exact replay accounting: `92` captured, `92`
   consumed, zero duplicates, and zero unconsumed transcripts.
7. Transport evidence is complete for every observed real attempt, including
   usage and finish reason.  The first control preserved a real
   `finish_reason=length` attempt; this is transport-attempt evidence, not a
   global-counter inference.

A final semantic-equivalence theorem is not claimed.  The 304 and 370 request
misses show that exact identity is selective rather than generic, and live QA
is invalid.  Under the explicit identity/oracle assumptions, the soundness
argument is by induction over certified calls within an episode and then over
ordered source publication; every miss delegates to the real provider, so no
false accept is permitted.  Live stochastic runs cannot claim bitwise graph
equality or a preserved response distribution.

Crash safety remains a separate obligation: atomic durable frontier advancement,
idempotent completion, side-effect fencing, and rejection of partial captures.
The current failure-path tests preserve partial transport evidence and reject
frontier advancement on failed work; held-out campaign repetition is still
needed before a broader final method claim.
