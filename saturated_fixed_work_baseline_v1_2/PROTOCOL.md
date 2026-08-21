# Saturated Fixed-Work Construction Protocol v1.2

This directory implements `SATURATED_FIXED_WORK_CONSTRUCTION_PROTOCOL_V1_2` in
isolation from v5, v4, S5, APC, and prior baseline artifact roots.

The construction input is the frozen four-history LongMemEval-S development
slice: 49, 49, 46, and 44 episodes, in the source order recorded by the
dataset manifest. `B0_NATIVE_SERIAL` awaits each native Graphiti
`add_episode` before admitting the next. `B1_NAIVE_WHOLE_UPDATE_ASYNC` creates
one complete `add_episode` task per source in source order, performs no await
inside the creation loop, and drains every task with exception attribution.
No application semaphore, worker pool, artificial arrival gap, compensation,
or MemBind scheduler is permitted.

`build_makespan` starts immediately before first admission and stops only after
all method-owned construction work is terminal and durable. Graph validation,
canonical export, correctness reduction, sealing, retrieval, Reader, and Judge
are outside that timer. Process-global vLLM metrics are block-attributable only
when both endpoint gauges are idle before and after and no other client is
observed.

Every formal block uses a unique request `cache_salt`, the same disjoint warmup,
the same provider/resource envelope, and a fresh namespace. The first valid
attempt is selected. Failed and partial attempts remain append-only.

The QA lane reads exactly the eight formal namespaces. Each namespace serves
four authored questions without reconstruction. Private references and gold
session identities are available only to post-retrieval metrics and the Judge.
Invalid QA remains in the primary accuracy denominator as incorrect. These are
development protocol-qualified results, not official MemoryAgentBench or
LongMemEval results.

## TDD and runtime gates

Every implemented capability has an observed RED before GREEN in the
append-only `tdd_evidence.jsonl`. The verifier checks timestamps, exit-code
semantics, journal order, required stages, and self-hashed order-only
amendments. `test_summary.json` binds the journal SHA-256 and records targeted
and full-suite results. Protocol tests run with `paper-eval-v3/.venv`; production
Graphiti, Neo4j, and live wrappers run with `membind-validation/.venv`. The
report wrapper uses the paper-eval interpreter because it performs only sealed,
offline reduction.

L0 admits later work only after a self-hashed preflight seal binds all tests,
the run manifest, model and cache canaries, fixed disjoint warmup, two-sample
idle evidence, resource parity, and a continuous 60-second sampler. That
sampler observes both vLLM endpoints plus provider GPU, runner CPU, runner
memory, and Neo4j at 1 Hz; missing sources, late cadence, active services, or
insufficient duration fail the gate.

L1 executes B0-A, B0-B, and B1 against the exact frozen 12-episode prefix in
new namespaces. L2 executes one isolated `07741c45` rehearsal and never feeds
the main tables. L3 executes the preregistered eight-block order. Resume starts
at the first block without a valid formal seal, skips sealed blocks, retains
failed attempts, and permits a new ordinal only after a terminal failure. L4
accepts only namespaces derived from the verified L3 formal seal, verifies each
canonical graph, and performs 4 read-only questions per namespace for 32 rows.

L5 reads sealed evidence only. It rejects placeholder or incomplete tables,
independently reduces the construction and QA evidence twice, requires
byte-equivalent results, writes the two development main tables and diagnostics,
then writes and verifies `FINAL_SEAL.json`. The completion marker is the last
write and is forbidden unless the verified final seal proves 8 construction
blocks and 32 QA rows.

## STOP recovery

`STOP_WITH_EXTERNAL_DIAGNOSIS.json` is immutable. When provider access later
supplies self-hashed live provider, historical provider, and runner Neo4j
physical evidence, `preflight` re-runs the historical/live resource gate and
may create the append-only `STOP_SUPERSEDED_BY_RESOURCE_RECOVERY.json`. The
supersession binds the raw-file SHA-256 and payload SHA-256 of the old STOP and
all three resource files. Every CLI invocation re-verifies those bytes and the
resource gate. A missing, malformed, mismatched, or later modified input yields
`STOP_SUPERSESSION_INVALID` and blocks all stages again.

The current run remains correctly stopped at L0: provider physical identity is
not available through the restricted execution channel. Its test summary also
keeps `tests_all_green=false` because `paper-eval-v3` has five clean-HEAD v4/MSEG
freeze failures and `membind-validation` has eleven clean-HEAD Neo4j evidence
hash errors. Therefore no preflight, qualification, rehearsal, formal block,
QA row, report, final seal, or completion marker is currently authorized.
