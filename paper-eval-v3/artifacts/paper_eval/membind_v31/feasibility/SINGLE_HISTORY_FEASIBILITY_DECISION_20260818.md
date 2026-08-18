# MemBind v3.1 Single-History Feasibility Decision

Date: 2026-08-18  
Scope: frozen block 0, `MemBind`, history `07741c45`, 49 episodes  
Role: feasibility/performance gate, not a final main-table row

## Decision

The correct next research unit is a complete 49-episode single-history
MemBind run. That unit has already been attempted once under the frozen
execution envelope, so an identical second run is not currently justified.
The existing attempt is a valid feasibility result with a provider-reliability
blocker, not a performance pass and not evidence that the state machine is
incorrect.

## Existing evidence

| Item | Observed value |
| --- | --- |
| Attempt | `membind-v31-smoke-20260818-004` |
| Plan | `membind-v31-dev-20260818-002` |
| History / source count | `07741c45` / `49` |
| Configuration | `C=2`, `W=2`, bind workers `1`, global LLM admission `K=2` |
| Completed prefix | source `0..30` durable (`31` publications) |
| Failure | source `31`, four provider retries, then terminal failure |
| Prompt envelope | `25,243` prompt tokens, requested `16,384` completion tokens |
| HTTP class | HTTP `200`, malformed/incomplete structured JSON |
| Parser error | `Unterminated string starting at line 1 column 44` |
| Server evidence | no context, KV-cache, RoPE, OOM, or transport-disconnect error |
| Attempt status | `FAILED_NON_REUSABLE` |

The detailed root-cause record is
[`FAILURE_ROOT_CAUSE_AUDIT_20260818.md`](../audits/FAILURE_ROOT_CAUSE_AUDIT_20260818.md).
The old attempt and its artifacts remain immutable and are not eligible for a
main-table row.

## Namespace and test status

The formal block-0 namespace was cleaned with an exact `group_id` scope before
any new attempt: `177 nodes / 355 relationships` before cleanup and `0 / 0`
after cleanup. Evidence is in
[`CLEANUP_EVIDENCE_BLOCK0.json`](CLEANUP_EVIDENCE_BLOCK0.json).

The focused single-history contract tests pass (`4 passed`); the related v3.1
live-block/orchestration tests pass (`13 passed` in the combined focused run).
The reusable wrapper is
[`run_membind_v31_single_history.py`](../../../scripts/run_membind_v31_single_history.py).
It reuses the sealed plan and the independent passing three-source smoke gate,
creates a new attempt root, and writes a checkpoint after every durable
publication through the existing block store. It marks output as
`SINGLE_HISTORY_FEASIBILITY_GATE_NOT_FINAL_TABLE`.

## Gate interpretation

The experiment currently supports:

1. The mainline method can process the first 31 publications under the frozen
   state/correctness contract.
2. The current 49-episode envelope is not qualified for an end-to-end
   performance claim because long-horizon structured output can fail at the
   frontier.
3. No conclusion about speedup, freshness, or final graph parity should be
   drawn from the partial attempt.

It does **not** support rerunning the same request unchanged. A subsequent
49-episode run requires a separately hash-bound serving/configuration repair
or an explicit protocol amendment (for example, a provider-side structured
output reliability fix). Changing schema, completion cap, parser semantics,
or silently repairing/truncating JSON would no longer be the frozen experiment
and must be recorded as a separate diagnostic lane.

## Next authorized action

After an authorized serving/configuration repair, run the wrapper in a fresh
attempt root and fresh exact namespace, in `tmux`, then inspect the checkpoint
at every publication. If the provider fails again, retain the new attempt and
stop with the failure source, error class, and token envelope; do not retry in
place or merge it into the main table.

