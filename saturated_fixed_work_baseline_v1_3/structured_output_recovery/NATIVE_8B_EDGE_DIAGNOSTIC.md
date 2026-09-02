# Native 8B Edge Diagnostic

Status: `REPRODUCIBLE_NATIVE_SCHEMA_OUTPUT_BLOCKER`.

This is an engineering-only diagnostic for the current source epoch at
`72633ca53dc5984d7e7f224921cbe97292410c05`. It used context `0`, source `0`
from the official five-record dataset and preserved the strict Native
Graphiti request path.

The node request completed normally (`finish_reason=stop`, 444 completion
tokens). The pinned `extract_edges.edge` request is hard-coded by the installed
Graphiti implementation to `max_tokens=16384`; the local 8B endpoint returned
exactly that budget, `finish_reason=length`, and a 50,902-character JSON body
that failed with `Unterminated string`. Graphiti's upstream JSON retry repeated
the same semantic request four times.

As a provider-only hypothesis test, the same edge request was allowed a
temporary 32,768-token budget. It again exhausted the entire budget
(`finish_reason=length`, 32,768 completion tokens, 104,767 characters) and
failed with an unterminated JSON string. This rules out a one-off 16,384-token
transport cap or transient queue/service failure; the unbounded native edge
schema permits the 8B model to continue emitting edges until the physical
budget is exhausted.

The active vLLM processes, HTTP transport, GPU, Neo4j Bolt service, and queue
metrics were healthy during both probes. Fixing this by paging/bounding the
Native edge schema, changing its request budget/stop policy, or switching to a
different model would change the declared A/B Native identity. Therefore the
current 8B profile cannot produce a valid three-arm canary under the frozen
contracts. Failed canary attempts remain preserved under
`/data/predator/ly/Mem/experiments/local-qwen3-8b-awq-dualreplica-v1/engineering-canary-20260902T0310`.
