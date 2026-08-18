# MemBind v3.1 Formal Attempt Root-Cause Audit

This is a read-only diagnostic record for the sealed attempt
`membind-v31-smoke-20260818-004`. It does not modify the attempt, its
namespace, the frozen method plan, or any result reducer.

## Classification

`FAILED_NON_REUSABLE`

The failure is a structured-output serving reliability failure at formal
block 0, history `07741c45`, source sequence `31`. It is not a transport
disconnect, HTTP context admission rejection, Neo4j failure, or telemetry
corruption.

## Evidence

| Item | Observed value |
| --- | --- |
| Attempt | `membind-v31-smoke-20260818-004` |
| Formal block | `0`, `MemBind`, `C=2`, `W=2`, `K_LLM=2`, `lookahead=2` |
| Failure source | `history=07741c45`, `source_sequence=31`, `request_kind=FRONTIER` |
| Frontier retries | 4 (`...00000498` through `...00000501`) |
| Prompt tokens on each failed retry | `25243` |
| Requested completion budget | `16384` |
| HTTP/transport status | all four attempts recorded `ok`; remote access log records HTTP `200` |
| Parser error | `Unterminated string starting at: line 1 column 44 (char 43)` |
| Graphiti retry policy | 4 attempts, then terminal failure |
| Server envelope | vLLM `0.26.0`, Qwen3-32B-FP8, `max_model_len=65536`, YaRN factor `2.0` |
| Server log evidence | startup reports `StructuredOutputsConfig(backend='auto')`, KV cache `202432` tokens, no OOM/KV/RoPE error |
| Remote log SHA256 (read-only snapshot) | `251fcb5b8f074e0ebef2e646a3819742127adc2d8e34f8e665bae71968c94a88` |

The current v3.1 LLM trace does not persist a trustworthy
`completion_tokens`/`finish_reason` field for this failed response. Therefore
the actual completion length is intentionally not claimed here. The evidence
supports only: HTTP 200 followed by an unterminated structured JSON body.

The sealed block checkpoint remains immutable and records 30 completed source
positions before the terminal failure:

```text
completed_source_prefix = 30
event_count = 212
terminal_status = INCOMPLETE_NON_MERGEABLE
source states = 31 PUBLICATION_DURABLE, 1 TERMINAL_FAILURE,
                2 PREPARED_DURABLE, 15 ARRIVAL
```

## Differential evidence

The bounded autoresearch candidate `c01` completed all 12 authorized probe
sources with `status=PASS`, `direct_violation_count=0`, and observed admission
bound `2`. This establishes that the method and transport path work for the
bounded prefix, but does not qualify the 49-source formal block.

The four failed requests have the same request identity and prompt token count;
they are not evidence of a changing predecessor state or a partial commit.
The failure occurs only after the long-horizon frontier prompt reaches 25,243
tokens.

## Adapter audit and TDD evidence

The production episode-loader fix is a narrow Python 3.12 dynamic-module
compatibility repair: it registers the temporary module in `sys.modules`
during `exec_module`, then restores/removes the alias in `finally`. It does not
change workload, Graphiti semantics, prompts, schema, serving parameters, or
the MemBind state machine.

TDD evidence after the repair:

```text
focused production executor: 8 passed
related v3.1 orchestration/autoresearch: 23 passed
full v3.1 offline regression: 173 passed, 1 warning
```

Full regression log:

```text
paper-eval-v3/logs/TDD_GREEN_MEMBIND_V31_AFTER_FAILURE_AUDIT_20260818.log
SHA256 2a74e580b467612fe9b69a163f4e0bb67abba0133bb7b7662cc9cad0b88bef84
```

This repair is valid for future executor startup, but it does not repair the
provider's malformed JSON at source 31. No safe adapter-only repair was found
that preserves the frozen `json_schema`, prompt/schema, 16,384-token budget,
model, and execution-envelope identity. JSON repair, switching to
`json_object`, changing the completion cap, changing the schema, or silently
accepting a partial response would all invalidate the current protocol.

## Decision boundary

The old attempt remains permanently `FAILED_NON_REUSABLE` and is not eligible
for a main-table row. A fresh formal attempt must not be started merely to
retry the same frozen request. It requires a separately authorized,
hash-bound serving/configuration repair or an explicit protocol amendment;
otherwise the correct scientific conclusion is that the current 49-source
formal envelope is not qualified under the frozen provider configuration.

