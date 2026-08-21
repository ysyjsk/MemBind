# Within-Version MEG Opportunity Oracle

- run: `membind-v31-opt-w4-meg-runtime-observe-20260821-011`
- capture: `085ddf5ce667f8bd9e9c92956312bedbf8456bc8334e5c51fb26039ed9fea1d7`

## Necessary Conditions

- at_least_two_legal_ready_llm_requests_observed: `True`
- choice_sets_that_can_affect_publication_completion: `True`
- observed_cache_affine_policy: `True`
- exact_version_and_effect_constraints_preserved: `True`
- publication_order_preserved: `True`
- state_ready_width_not_used_as_llm_choice_set: `True`
- queue_depth_not_used_as_llm_choice_set: `True`
- active_request_count_not_used_as_llm_choice_set: `True`

## Resource Model

Resource capacities are fixed from the capture: LLM K=2; DB/CPU/OPAQUE are non-binding because no shared capacity contract is observable. OPAQUE means no independent embedding/backend span was observed.

## Source Results

| source | observed latency ns | critical path ns | LLM work ns | K=2 LB ns | CACHE_AFFINE ns | FIFO ns | criticality-first ns |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0 | 4056553253 | 3911554289 | 3827741485 | 1913870743 | 3911554289 | 3911554289 | 3911554289 |
| 1 | 21188887160 | 19569198586 | 20978083119 | 10489041560 | 19569198586 | 19569198586 | 19569198586 |
| 2 | 10535193383 | 8321289370 | 11545277943 | 5772638972 | 8321289370 | 8321289370 | 8321289370 |
| 3 | 17621039542 | 16996280450 | 17358920938 | 8679460469 | 16996280450 | 16996280450 | 16996280450 |
| 4 | 9394929979 | 8395357282 | 9164557971 | 4582278986 | 8395357282 | 8395357282 | 8395357282 |
| 5 | 27892289193 | 25553304946 | 31628016784 | 15814008392 | 25553304946 | 25553304946 | 25553304946 |
| 6 | 41491921521 | 34408006056 | 44588360690 | 22294180345 | 38385310878 | 38385310878 | 36297334347 |
| 7 | 33268215545 | 27704926339 | 36853935644 | 18426967822 | 27704926339 | 27704926339 | 27704926339 |
| 8 | 108681411206 | 101937209972 | 118868110306 | 59434055153 | 101937209972 | 101937209972 | 101937209972 |
| 9 | 89208380020 | 82294262559 | 46551430588 | 23275715294 | 112792175345 | 112681066357 | 87928117643 |
| 10 | 94860785550 | 81024954100 | 87132462882 | 43566231441 | 139882199542 | 139835823322 | 94041920476 |
| 11 | 171314474939 | 129230770013 | 174459169961 | 87229584981 | 177027694499 | 176997741439 | 134503585825 |

## Admission Choice Sets

- decisions: `236`
- decisions with >=2 legal-ready candidates: `129`
- completion-affecting choice sets: `128`

A legal-ready candidate is certified by OPERATOR_READY, exact predecessor publication for STATE_DERIVED work, and prior subrequest completion. Queue depth and active count are reported only as context.

## Criticality Inversions

- inversion count: `116`
- duration ns: `{'count': 116, 'p50': 6101789014.0, 'p95': 13953542508.0, 'max': 13996329300, 'sum': 865982975096, 'mean': 7465370474.965517, 'stdev': 4072809343.7002215}`
- involved service ns: `{'count': 116, 'p50': 1201128431.0, 'p95': 1397202358.0, 'max': 1557092727, 'sum': 134410595959, 'mean': 1158712034.1293104, 'stdev': 163620268.05938163}`
- theoretical penalty ns: `{'count': 116, 'p50': 943616805.0, 'p95': 5860867249.5, 'max': 6452425580, 'sum': 185229871847, 'mean': 1596809240.060345, 'stdev': 1548573317.3990064}`

## Decision

`STOP_LLM_ADMISSION_NOT_CAUSAL`

The theoretical lower-bound gap is dominated by dependency/backend schedule slack; the legal-ready LLM ordering delta is not the majority of available headroom.

No scheduler or admission policy was implemented; all schedules are offline projections over the sealed trace.
