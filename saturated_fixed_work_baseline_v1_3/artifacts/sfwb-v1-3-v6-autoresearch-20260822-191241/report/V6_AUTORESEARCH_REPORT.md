# V6 Autoresearch Report

## Conclusion first

The complete real Graphiti V6 full-history qualification is GREEN and sealed.
Four 46-source arms ran on frozen `8000/8001`: control→V6 and V6→control.
All arms reached durable frontier `45`, passed provider/frontier proof, and
used the pinned Graphiti 0.29.3 native path.  The machine-checked result is
`main/V6_MAIN_COMPARISON.json`.

The result is intentionally `QUALIFICATION_ONLY`, not a general performance or
correctness claim.  Two paired timer deltas (control minus candidate) were
`1533.223 s` and `1011.468 s`; candidate replay was exact `92/92` in both arms,
but request misses were `304` and `370`, and QA remains `INVALID_RETAINED`.

## What was implemented

- A V6-owned campaign root, resumable `RUN_STATE.json`, append-only
  `V6_AUTORESEARCH_LEDGER.jsonl`, method/proof drafts, and decision cards.
- An exact L0 critical-path reducer that reconstructs the sealed V5 6071bd76
  timer (`1,522,517,673,483 ns`) from journals and native intervals.  It reports
  a `206,530,169,066 ns` source-0 preparation prefix, a
  `1,315,798,013,061 ns` native occupied chain, and a `187,354,224 ns`
  inter-native gap total.  Child phase totals are attribution-only.
- A separate `run_v6.py` executable with explicit history/policy/full-history
  schema, frozen `8000/8001` endpoint identity, gate-first failure behavior,
  shared FrontierExecutor, provider arbiter, private request observation, and
  proof-before-seal artifacts.
- Strict request observation and proof validators.  Any changed request field
  is a miss; certified replay is the only provider-free path.

## Tests

`34` focused V6 tests (including the sealed-main comparison reducer) and `201`
saturated v1.3 tests pass.  Compileall and `git diff --check` pass.  The baseline
formal seal and P8 seal were read-only and unchanged.

## Full-history evidence

Both pair orders used fresh namespaces and the same executable/configuration.
The first control/V6 pair measured `2884.676 s` vs `1351.453 s`; the reverse
pair measured `1326.745 s` vs `2338.213 s`.  Candidate transport attempts were
`396` and `462`; control attempts were `478` and `474`.  All usage and finish
reasons were observed, with zero transport errors.  The first control retained
a real `finish_reason=length` attempt followed by Graphiti retry recovery.

Provider proof was unchanged in all arms: capacity `8`, max outstanding `8`,
future max outstanding `7`.  Frontier proof was ordered and durable through
source `45`; overlap was true in both candidate arms (106 and 125 pairs).

## Method status

The resulting narrow method is certified extraction replay with frontier-aware
provider admission.  It changes no backend scheduling and leaves every miss
and non-certified native request on the real provider.  It is not generic
native replay: the broad request drift is an explicit negative result.  No
quality or freshness claim is made because QA remains `INVALID_RETAINED`.

## Completion and next campaign

The current V6 campaign is sealed at `R12`.  The next informative experiment is
held-out histories with fresh same-time native B0/V5 controls and valid QA; do
not treat this single development history as a general estimate.  Keep the
baseline, P8, workload, backend configuration, and QA status unchanged.
