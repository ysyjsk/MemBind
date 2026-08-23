# V6 Autoresearch Report (Current State)

## Conclusion first

V6 implementation and provider-free evidence are GREEN, but the real
`8000/8001` Probe A/B and full `6071bd76` matched campaign have not started.
The current `membind-validation/CURRENT_STATE.json` denies `LiveAction.FORMAL`
with `action_not_authorized`.  No Graphiti namespace or provider request was
created by this V6 attempt.

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

`29` V6 tests, `196` saturated v1.3 tests, and `60` pinned instrumentation/
workplan tests pass.  Compileall and `git diff --check` pass.  The baseline
formal seal and P8 seal were read-only and unchanged.

## Live preflight

Direct no-proxy model catalogs and idle metrics passed for construction
`8000`/embedding `8001`; Neo4j 5.26.0 HTTP/Bolt canary passed with project
credentials; restricted remote status returned `readonly liuyi access OK`.
Remote process argv/watchdog identity is not observable through the permitted
read-only command set.  This is an evidence limitation, not a service outage.

## Method status

The `v6` arm is labelled `V6_REQUEST_STABILITY_PROBE` and
`claim_status=QUALIFICATION_ONLY`.  It is not a final performance treatment:
the attributes/summary native phase still needs complete real request identity
evidence.  No quality or freshness claim is made because QA remains
`INVALID_RETAINED`.

## Required continuation

After the formal state transition is authorized, run a fresh 2-source
matched-control and request-stability probe on `8000/8001` in detached tmux,
then one AB/BA single-request interference probe.  Use those observations to
select exact replay, certified repair, native input/delta reduction, or the
null branch; do not start the 46-source campaign before that selection.
