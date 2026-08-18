# MemBind v3.1 W=4 Diagnostic Pilot Result

Date: 2026-08-18  
Status: `PASS_DIAGNOSTIC_ONLY_NON_MERGEABLE`  
Lane: scheduler/admission observability optimization lane

## Scope and authority

This report records exactly one bounded live pilot authorized by
`MEMBIND_V31_OPTIMIZATION_WORKPLAN_20260818.md`. It does not amend
`MemBind_FINAL_METHODOLOGY_v3.1_FROZEN.md`, the formal v3.1 workplan,
`V31_METHOD_PLAN.json`, any previous attempt, the formal reducer, or the paper
main table. The result is permanently diagnostic-only and is not a baseline or
method result.

Frozen pilot identity:

```text
pilot_run_id:       membind-v31-opt-w4-20260818-001
attempt_id:         membind-v31-opt-w4-20260818-001-attempt-001
history:            07741c45
source_sequences:   0..11
compile_workers:    2
lookahead:          4
bind_workers:       1
global LLM K:       2
policy:             FRONTIER_FIRST_CACHE_AFFINITY
namespace:          pev3-opt-membind-v31-w4-20260818-001-membind-07741c45
artifact status:    DIAGNOSTIC_ONLY_NON_MERGEABLE
merge authority:    NONE_NON_MERGEABLE_OPTIMIZATION_PILOT
```

No formal namespace, formal cache salt, held-out data, or old failed attempt
was reused. The run was launched in a dedicated tmux session and the session
exited normally after the runner returned `PILOT_PASS_DIAGNOSTIC_ONLY`.

## TDD and preflight gates

The focused offline suite was run immediately before the live call:

```text
25 passed in 1.51s
```

The previously completed v3.1 offline suite remains:

```text
2251 passed, 1 warning
```

Read-only live preflight passed for:

- restricted `ssh zju-liuyi 'status'`;
- construction `/v1/models`: `qwen3-32b-fp8`, `max_model_len=65536`;
- embedding `/v1/models`: `qwen3-embedding-0.6b`;
- local Neo4j ports 7474/7687;
- fresh pilot namespace: `nodes=0`, `relationships=0`, `episode_names=[]`.

The only Neo4j messages were deprecation warnings for the existing `CALL {}`
subquery syntax; they were not query or write failures.

Graphiti also emitted non-fatal diagnostic lines of the form
`Target entity not found in nodes for edge relation` for some extracted
relations. They did not raise an exception, alter the frozen prompt/schema,
trigger a retry or repair, or violate the pilot publication contract. They are
retained in the console log and should be treated as a semantic-quality
diagnostic for any future aligned quality analysis; this pilot makes no quality
claim and does not silently reinterpret those warnings as successes.

## Execution outcome

All 12 sources reached the durable publication boundary in source order:

```text
source states:       12 × PUBLICATION_DURABLE
publication order:   0,1,2,3,4,5,6,7,8,9,10,11
direct violations:   0
observed max LLM in-flight: 2 (configured K=2)
failure artifact:    absent
```

The post-run namespace probe observed:

```text
nodes:            89
relationships:    201
episode names:    07741c45::episode::0000 ... 07741c45::episode::0011
```

This is a diagnostic graph-state observation only; it is not a claim of
semantic parity with a formal baseline.

## Performance and telemetry

The runner's sealed performance summary is:

| Metric | Value |
|---|---:|
| Published episodes | 12 |
| Makespan | 698,777,570,889 ns (698.778 s) |
| P50 freshness | 33,115,230,273 ns (33.115 s) |
| P95 freshness | 238,840,735,985 ns (238.841 s) |
| P99 freshness | 238,840,735,985 ns (238.841 s) |
| Max freshness | 238,840,735,985 ns (238.841 s) |
| Goodput | 0.01717285 episodes/s |
| LLM telemetry records | 861 |
| LLM requests (submitted/start/terminal) | 279 / 279 / 279 |
| LLM request terminal status | 279 `ok`, 0 errors |
| Submitted prompt-token count | 726,365 |
| Compile / Frontier submitted tokens | 323,756 / 402,609 |
| Scheduler snapshots | 84 |
| Admission snapshots | 582 |
| Max admission waiting count | 19 |
| Max active admission count | 2 |
| Prepared artifacts | 12 |
| Prepared raw nodes / raw edges | 80 / 125 |

The raw trace is retained as the source of truth. Token prefix identifiers and
trace HMACs are stored as identities; raw prompts, completions, credentials,
and user content are not included in the report.

## Scheduler/admission diagnosis

The offline `QUEUE_DIAGNOSTIC.json` analyzer distinguishes scheduler-ready work
from transport waiters. The relevant observations are:

```text
max legal ready Compile count:       1
max Prepared ROB occupancy:          1
legal-ready duration:                6,258,028 ns (6.258 ms)
work-conservation candidate time:    6,258,028 ns
window-limited duration:             0 ns
arrived-beyond-lookahead duration:   0 ns
max arrived-beyond-lookahead count:  0
compile waiter under capacity:       0 ns
frontier waiter under capacity:      0 ns
admission under-capacity with waiter: 0 ns
admission under-capacity without waiter: 431,531,362,894 ns
```

The long `under-capacity-without-waiter` interval means that the global gate was
often below K because no additional request was waiting. It must not be
interpreted as an admission-controller bottleneck. Likewise, the observed
transport waiting count (maximum 19) is not evidence of legal Compile-ready
work: the scheduler snapshots show no work outside the lookahead window and
only a negligible legal-ready interval.

Therefore the strongest defensible conclusion is:

```text
NO_W2_READY_POOL_STARVATION_OBSERVED
NO_LOOKAHEAD_LIMITATION_OBSERVED
NO_ADMISSION_UNDER_CAPACITY_WITH_WAITER_OBSERVED
```

This pilot does not justify Snapshot Resolve, OCC/MVCC, read-set validation,
selective repair, prompt changes, JSON repair, or token-cap changes. It also
does not justify claiming a W=4 speedup: there is no aligned W=2 control in
this diagnostic-only run.

## Plan decision

Under the frozen optimization workplan's stop rule, the W=4 lane is stopped
after this pilot. The data do not show legal ready work that W=2 excludes, so a
larger-W live experiment would not answer the current research question. Any
future scheduler or locality hypothesis requires a separately authorized
offline design and a new fresh identity; it must not be appended to this
attempt or merged into the formal main table.

## Artifact locations and integrity

Pilot root:

```text
paper-eval-v3/artifacts/paper_eval/membind_v31/optimization/pilots/
membind-v31-opt-w4-20260818-001/
```

Important artifacts:

- `PILOT_CONTRACT.json`
- `manifest.json`
- `checkpoint.json`
- `events.jsonl`
- `llm.jsonl`
- `queue.jsonl`
- `private/prepared/00000000.json` through `00000011.json`
- `QUEUE_DIAGNOSTIC.json`
- `result.json`

The independent post-run verifier reported `all_pass=true` for contract
identity, manifest/checkpoint/result/diagnostic hashes, event hash chain and
sequence numbers, 12 prepared-artifact hashes, publication count, and the
non-mergeable status contract.

SHA-256 (file-level):

```text
PILOT_CONTRACT.json    fbcb5cc58bf6562d047b99dd566e055c752bba3119638e479ee40d2add253be9
manifest.json          6a14cd4a32ae93afd83854613a84743d0f0dcce9fc0bc83dd631652a95c810dc
checkpoint.json        1aa4621a47046437a637dc2efc0fe7a6d76d58c93c542bb39d8f9090ca5f9d7f
result.json            a4440e2421add6dc74cf5708e6630785b516a980503d47cfeec39dc65c93a46c
QUEUE_DIAGNOSTIC.json  e0b1e6a767ec87d792edee6632e8bf40ac0aafa670a0f4d57d78c9ae6b1534ac
events.jsonl           54a76f5aac2ba4005036e441b74c1351de4ec20ad41ad17edd8e32116523bd38
llm.jsonl              55ff7d48c9ffba068b1ead781daf7bf5dd2bfe5681fa897937311e7f82dee6eb
queue.jsonl            c33a9be35d0c5327617cc6769a8fa441b799020af2dc34303ab87deb0bda3f83
```

The tmux console log is retained at:

```text
paper-eval-v3/logs/MEMBIND_V31_OPT_W4_20260818_001.log
```
