# Local Protocol State Audit

Date: 2026-08-21
Repository: `/data/predator/ly/MemBind`
Authority: current local checkout (`main`, `072a69b2ed02424f769c52a024b5cfc578385eb3`)

## PUBLIC_MAIN_STATE

The checked-out `main` tree contains the v1.3 protocol, simplified campaign,
v1.3 adapter, v5 offline analyzer/fingerprint modules, the historical v1.2
reuse layer, and sealed development artifacts. The public tree is the Git
authority for tracked files at this revision.

## LOCAL_WORKSPACE_STATE

The active path is:

```text
saturated_fixed_work_baseline_v1_3/src/
  saturated_fixed_work_baseline_v1_3/simple_campaign.py
  saturated_fixed_work_baseline_v1_3/membind_adapter.py
  saturated_fixed_work_baseline_v1_3/membind_v5/
saturated_fixed_work_baseline_v1_3/tests/
```

The local branch is clean apart from this workplan and the new protocol-cleanup
changes. No existing sealed root was edited. The repository contains tracked
Python bytecode files; they are identified for index cleanup and are not an
execution dependency.

## LOCAL_ONLY_IMPLEMENTATION

Before this round, the v5 analyzer, first-divergence analyzer, semantic
fingerprint helper, fingerprint qualification, and simple campaign extension
were present in the local tree. This round adds the v1.3 backend/client
contracts, lifecycle contract, provider-free serial certification, and passive
real-seam observer.

## HISTORICAL_ONLY_COMPONENTS

The v1.2 resource and production modules, the old v3.1 implementation under
`paper-eval-v3/src/paper_eval/membind_v31/`, and all existing sealed artifact
roots are historical or reusable dependencies. The old v3.1 source is audited
only; it is not modified or used as a new source of truth.

## ACTIVE_EXECUTION_PATH

The current live entry point is `simple_campaign.py`. Its L0 checks endpoints,
Neo4j canaries, workload, runner, instrumentation, fixed disjoint warmup, and
backend idle. Its B0/B1 execution delegates stable v1.2 execution primitives;
the MemBind extension uses `membind_adapter.py`. No provider is contacted by
the cleanup tests or certification fixtures.

## DECISION

The local path is sufficiently identified for protocol cleanup. No
`STOP_PROTOCOL_STATE_AMBIGUOUS` condition was found.
