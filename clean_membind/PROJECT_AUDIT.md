# MemBind Project Audit

## Scope and current conclusion

This audit is a read-only reconstruction of the existing repository at
`999cdeac98fca5666fed63201d61b69f1952019c`. Historical implementations and
artifacts remain in place. Nothing in this clean tree is evidence of a new
formal result yet.

MemBind studies whether work that a graph-memory system will need for a future
episode can be prepared early, validated against the authoritative request,
and reused without changing Native publication order. The method is not a new
LLM, a new graph algorithm, or a response-repair layer.

## A/B/C reconstruction

* **A, Serial Native:** each episode is sent through the upstream Graphiti
  `Graphiti.add_episode` path in source order.
* **B, Async Native ceiling:** the same Graphiti object, model, adapter, and
  database are used, while independent Native calls are admitted concurrently.
  B is an upper-bound/ceiling baseline, not a quality-improving algorithm.
* **C, MemBind:** future Native preparation is started early. At the frontier,
  complete request identity and payload integrity are checked. A valid result
  is reused; a stale, missing, or failed result falls back to Native. Durable
  publication remains in source order.

The old V6.1 implementation confirms that preparation entered Graphiti's
upstream `extract_nodes` and `extract_edges` seams, while final publication
continued through the full Graphiti path. The new core expresses this contract
without importing that historical package.

## Workload and adapter

The frozen `MAB_ROLE_AWARE_LOSSLESS_8192_V1` adapter limits each transport
chunk to **8,192 characters**, not tokens. It serializes each public session as
role markers and contiguous text; concatenating chunks reconstructs the exact
session. A chunk is one Graphiti episode with a strict predecessor chain. This
is a normal Graphiti ingestion unit and does not alter the benchmark's QA
labels or source order.

The adapter therefore remains **KEEP**. Changing the limit or replacing the
workload after the Qwen failure would conceal the observed deployment risk.

## Failure at source 79

The terminal P2 artifact is:

`/data/predator/ly/Mem/experiments/local-qwen3-14b-awq-dualreplica-v1/upstream-l2-full-h0-p2-20260904T234000Z`

At source sequence 79 (`s0079`, `extract_edges.edge`) the telemetry records
`prompt_tokens=14633`, `completion_tokens=16384`, `finish_reason=length`,
invalid JSON, and zero provider retries. The request is an upstream Graphiti
edge extraction request, not a MemBind-only request. Previous episodes,
retrieved candidate nodes/edges, and the accumulated extraction state expand
the prompt; the edge response then exhausts the fixed completion budget. The
best-supported classification is **PROMPT_STATE_GROWTH + OUTPUT_LIMIT /
STRUCTURED_OUTPUT_FAILURE** in the Qwen3/vLLM deployment. It is not evidence
that the benchmark adapter is wrong, nor evidence for a MemBind quality claim.

Historical V6/V7 artifacts also show that smaller local models and custom
structured-output workarounds repeatedly produced truncation or invalid JSON.
Those artifacts are valuable negative/deployment evidence and are not promoted
to the clean Native.

## Runtime and governance findings

The old tree accumulated qualification gates, provider routing, response
recovery, finite-pair experiments, and multiple finalizers. They made identity
auditing harder and did not repair the strict upstream failure. The clean tree
keeps only an identity hash, append-only telemetry, and explicit cell envelopes.

The old Qwen3/vLLM services may remain running for legacy analysis, but they are
not a dependency of `clean_membind` and must not be silently reused for the new
Native decision.

## External evidence

The official Graphiti v0.29.3 README documents `OpenAIGenericClient` for
OpenAI-compatible local endpoints, including Ollama, vLLM, llama.cpp, and LM
Studio. Its local example uses DeepSeek R1 7B plus Nomic embeddings and warns
that structured-output reliability varies by provider and model.

`Flo976/graphiti-mcp-ollama` is an independent runnable deployment using
Graphiti's MCP server, Ollama `qwen2.5:14b`, Nomic embeddings, and a low
concurrency semaphore. Its README demonstrates a persistent service, but does
not establish LongMemEval-scale continuous ingestion; that gap is why Step 2
and Step 3 validation remain mandatory.
