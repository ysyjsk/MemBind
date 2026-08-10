# Native Graphiti Characterization Research Reset Report

## Outcome

The research priority has been reset from solution-first M2 validation to a
problem-first Native Graphiti characterization lane. The authoritative workplan
is:

```text
MemBind_NATIVE_GRAPHITI_CHARACTERIZATION_WORKPLAN_v1.0.md
protocol_id=native-characterization-v1.0
sha256=c981490a7266a4672affdebf7b447404908c95fae0679725d0e479c5595fe805
```

No new live characterization experiment was started during this document/TDD
turn. Existing H0/M1/M2 code and artifacts remain preserved as a frozen
exploratory solution lane.

## Why the reset is scientifically necessary

The previous order attempted to validate `Parallel Compile + Latest-State Bind`
before establishing the native construction bottleneck and its dependency
structure. The corrected order is:

```text
Native construction breakdown
-> evidence-based state-dependency map
-> Sync/Async online tension
-> naive whole-update parallel baseline
-> problem verdict
-> only then compare candidate mechanisms
```

The workplan explicitly allows `NOT_SUPPORTED`, a simpler whole-update baseline,
DB/index optimization, LLM serving/batching, ordinary async scheduling, OCC, or
another mechanism. It does not automatically return to M2.

## Source audit findings that changed the plan

The pinned Graphiti `v0.29.3` / commit `021d3a5` path shows that extraction is
not episode-only: `add_episode()` first retrieves previous episodes and supplies
that context to node and edge extraction prompts. The new dependency taxonomy is
therefore:

```text
D0 episode-only
D1 immutable source/history-prefix
D2 latest materialized entity/edge graph
D3 mutation/publication
unknown
```

Additional implementation findings:

- current project `run_native_serial` is already arrivals + one background worker,
  so it is Async-Serial and cannot serve as the missing blocking Sync baseline;
- the project factory installs deterministic search stabilizers and an in-process
  embedding cache, so U0 upstream-qualified Native and U0-S project-stabilized
  guardrail must be reported separately;
- current tracing records whole-episode latency and LLM/embedding/DB counts but
  lacks qualified phase, request, search, transaction, and commit durations;
- nested and concurrent calls require parent/child spans, interval-union and
  exclusive-time accounting; summing call durations would double-count work;
- an oracle miss under naive parallelism proves trajectory divergence only, not
  automatically a final graph correctness failure.

## Bounded first wave

```text
E1/E2: 4 calibration histories x 1 shared Native trace = 4 blocks
E3: 2 methods x 5 frozen rho loads x 1 screening repetition = 10 blocks
E4: C={1,2,4,8} x 1 screening repetition = 4 blocks
```

This wave is descriptive signal finding. It makes no significance or held-out
paper-evaluation claim. Any confirmation requires a new frozen plan.

## TDD evidence

| Evidence | Result | SHA256 |
|---|---|---|
| `artifacts/tdd/native_characterization_workplan_document_contract_red_001.log` | expected RED, 9/9 missing-plan failures | `e727910218087ba4a4de5488bf517ee823d6518ab1498757640fa429429022e6` |
| `artifacts/tdd/native_characterization_workplan_execution_matrix_green_007.log` | document contract GREEN | `5af4dc3fbc87b1c96254d431e13952940ff0c3678813d657b9ba1daace15b525` |
| `tests/test_native_characterization_workplan.py` | final 12/12 GREEN | `35356ae528cff08a318cb24c4d4b4d4b7b59f2d7822efb7a2cf3329242dc227f` |
| `artifacts/tdd/native_characterization_final_full_offline_regression_009.log` | 585 total: 577 pass, 7 fail, 1 error | `a13d9cd817119e17fe25d354397f4666ffbed8cd4aa6f049ac9e3b0ad3c57c46` |

The full-suite residuals are pre-existing R5/R6 state-test drift: legacy tests
expect replacement-003 or the old action, while the unchanged machine state is
already replacement-004. No failing traceback references the new workplan or
its pointer/test files. The historical state was deliberately not reverted:

```text
CURRENT_STATE.json sha256=fb57c0edb6388c2ae94c6ba338e1671c39fa08e218cfc96566ee4d315b2e231d
```

## Next executable step

The next step is offline only:

1. RED-test an explicit state transition that freezes the old solution lane and
   revokes its replacement-004 live grant without rewriting history.
2. RED-test nesting-safe characterization spans, interval union, source mapping,
   concurrent attribution, sanitization, and wrapper parity using fake clients.
3. Implement minimal GREEN instrumentation and pass focused/full offline gates.
4. Only then run one C0 Native episode canary. Any `vLLM unreachable` condition
   is checkpointed and reported immediately.

No API key, `.env` content, Authorization header, raw prompt, raw response, or
remote write appears in this report or its evidence.
