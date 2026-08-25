# T1-T6b Proof Audit

This is a proof-obligation audit, not empirical evidence. Each obligation is
paired with an executable negative/positive test in
`tests/test_membind_v7_theory.py`.

| theorem | induction/invariant | executable falsifier | current closure |
|---|---|---|---|
| T1 | every Read inherits one SnapshotToken; A2 forbids writes | mixed versions; write before seam | conditional |
| T2 | projection equality by primitive-local delta extractor | omit embedding/epoch field | scoped conditional |
| T3 | complete domain, phantom candidates, cutoff, tie, rank and epoch invariants | insert outside old domain; deletion of kth; boundary tie; epoch change; missing BM25 contract | guarded conditional |
| T4 | topological induction over six Dep kinds | previous-window or predecessor change | conditional |
| T5 | finite worklist plus actual-repair canonical-output reconvergence | missing repair result; output-changing branch; cycle/max bound | conditional |
| T6 | memo-free counterpart expansion and canonical trace induction | order-only divergence; UNKNOWN treated as stable | conditional |
| T6b | step-local equivariance over exact continuation-observable K | ignored endpoint UUID; stale frontier; missing embedding; hidden saga/community work | closed for guarded default tail; other tails UNKNOWN |

`Complete_rho` is never inferred from a final-state coincidence. Unknown backend
contracts are represented explicitly and cause fresh execution. The d=1
restriction is part of every statement; no multi-delta composition theorem is
claimed.
