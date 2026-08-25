# Conditional M2 Extension

T7 requires a staged plan with source sequence, exact base frontier, closed
predicate preconditions, complete effects and an idempotency key. The Apply
step may not call an embedder, search, clock, saga query or control oracle.
The reference validator rejects any hidden read and any frontier mismatch.

T8 models `ABSENT -> PREPARED -> CERTIFIED -> APPLYING -> COMMITTED`. A crash
without a durable receipt cannot advance the frontier; a committed state must
have a receipt. These are necessary invariants, not a claim that Graphiti's
bulk-plus-saga tail is a closed Apply. P7 currently treats that tail as
UNKNOWN/unsupported, so M2 remains blocked unless a later refinement closes
every embedding, bulk, saga and recovery point.
