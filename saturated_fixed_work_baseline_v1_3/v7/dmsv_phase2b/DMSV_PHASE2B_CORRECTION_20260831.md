# DMSV Phase 2B correction report

Date: 2026-08-31
Input commit: `58a925f372db1a095c9e90b969ad74d101c4e96a`
Parent: `f91a0500beb87d5013644442e135e6d3afb4507c`
Graphiti: `0.29.3`

## Final state

`FINAL_STATE=BLOCKED_DOMINANT_REQUEST_INEVITABILITY_UNPROVEN`.

This is an append-only correction to the previous Phase 2B record. The old
report, old closure JSON, old delta matrix and old ledger remain untouched.

## Corrections to prior reporting

The input commit contains 32 changed files (`3075` insertions, `62` deletions),
not nine principal files. `git show --check` on the input commit fails because
the old report has trailing whitespace on lines 3 and 4. The old report's
unqualified diff-check PASS is therefore corrected to
`prior_commit_diff_check=FAIL; reason=trailing_whitespace_in_phase2b_report`.
The old frozen workplan hash `7fc39e6d...` is not reproducible from the current
checkout and is recorded as `OLD_FROZEN_WORKPLAN_HASH_UNREPRODUCIBLE`; input,
frozen, post-B4 and correction hashes are kept as separate fields.

## R3 causal repair

The preregistration was written and hashed before the R3 tests:
`DMSV_B1_CLOSURE_REPAIR_PREREGISTRATION.json`, SHA-256
`5044d7c6dd0651fdc9043692060536f93bd28facc1fff1d95102a956922499b3`.

The existing provider-free matrix remains a `SENSITIVITY` result. It calls the
actual Graphiti 0.29.3 `prompt_library.dedupe_nodes.nodes` builder and shows that
controlled mutations can change the canonical request. It does not prove that
those fields change in a real state transition.

The non-held-out development observer supplies one useful adjacent pair
(`history=b6019101`, `source_sequence=4`): state version `3 -> 4`, the previous
episode window grows from three to four IDs, the projection digest changes, and
the recorded `dedupe_nodes.nodes` request and message digests change. However,
the observer does not preserve the complete retrieval reference time,
group/source binding, `last_n`, independent request-binding digest,
schema/index epochs, or decoding contract. No value is fabricated to fill these
gaps. The pair is therefore `REAL_PAIR_WITNESS_MISSING_FIELD`, not a complete
inevitability witness. Details and exact digests are in
`DMSV_DOMINANT_REQUEST_CAUSAL_WITNESSES.jsonl` and
`DMSV_ADJACENT_STATE_REQUEST_CAUSAL_PROOF.md`.

Accordingly, the stronger claims
`DMSV_NATIVE_NODE_NULL_DOMINANT_CALL_ALWAYS_DIRTY` and
`DMSV_DOMINANT_CALL_UNAVOIDABLE` are not admitted by this correction. No native
batch split is introduced.

## R5 paired failure attribution

Parent and current commits were archived into independent temporary checkouts.
The four source-binding test modules contain 25 tests; each checkout produced
18 passes and the same seven failures, all failing at the frozen source-binding
guard. The expected `graphiti_observer.py` digest is
`3214ba84...`; parent is `56793070...` and current is `d037b00e...`. The failure
was already present in the parent (`PREEXISTING_FAILURE`) and the input commit
also changed the bound file (`COMMIT_INDUCED_SOURCE_HASH_DRIFT`). Ignored
virtualenv/external fixture paths prevent a clean archive from reproducing the
historical repository-wide `718 passed, 7 failed` count; that limitation is
classified as environment/provenance, not silently treated as a green result.

## Authorization and stop

R3 provider-free tests pass after witness materialization. Provider calls and
database writes remain zero; held-out histories remain untouched. B2 Top-K
maintainer, B3 affectedness/economics, Phase 3A/3B observer, live treatment,
and scheduler/lane/quota/future-cap search are all unauthorized. The correction
stops at B4 with `MAIN_TRACK_CANDIDATE=false` and all live authorization flags
false.

The minimum falsifiable reopening condition is a newly captured, complete
adjacent-state witness containing every preregistered retrieval, request
binding, epoch, and decoding field, plus an independently proven legal native
batch-localization seam. Until then, the correct conclusion is blocked rather
than a universal inevitability claim.
