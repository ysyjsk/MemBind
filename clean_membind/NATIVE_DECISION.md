# Native Decision

## Decision status: `NATIVE_NOT_READY`

No model/runtime combination tested on this host satisfies the Native freeze
gate. The clean implementation is ready for a future backend, but the formal
three-arm experiment is **not authorized** and no formal cell has been started.

## Candidates tested

The initial primary candidate was tested as follows:

| Component | Frozen choice |
| --- | --- |
| Graph implementation | upstream `graphiti-core==0.29.3`, commit `021d3a57d511f21b10adaf7fa923bd5c1fce5e9d` |
| LLM client | Graphiti `OpenAIGenericClient` |
| Local runtime | Ollama OpenAI-compatible endpoint, `http://127.0.0.1:11434/v1` |
| LLM | `qwen2.5:14b` (open weights; exact Ollama digest must be recorded before freeze) |
| Embedding | Ollama `nomic-embed-text`, 768 dimensions |
| Structured output | `json_schema` first; no repair/retry algorithm is added |
| Database | existing Neo4j deployment, with URI/version recorded at freeze |
| GPU placement | one Ollama model server at a time during validation; two identical replicas only after resource measurement |

The official `deepseek-r1:7b` example was tested as the single backup, not as a
model zoo.

## Evidence and rejection

* qwen2.5:14b + json_schema passed one short episode, then failed at global
  chunk 18 with invalid JSON after four Graphiti retries (`max_tokens=2048`).
  At `max_tokens=16384` the same state produced no durable result for 51
  minutes and exhausted a local runner.
* qwen2.5:14b + json_object immediately returned the schema document rather
  than an `ExtractedEntities` object.
* deepseek-r1:7b + json_schema passed one short episode, then failed at global
  chunk 7 with an unterminated JSON string after four retries.

These are real structured-output/deployment failures in the untouched Graphiti
path. They are not repaired in clean code and do not justify changing the
benchmark adapter. The external project evidence remains useful, but short
MCP smoke tests do not establish LongMemEval-scale reliability.

## Freeze gate

An eventual candidate may become `NATIVE_READY_FOR_MAIN_EXPERIMENT` only after:

* the external deployment shape succeeds on this host;
* the real adapter succeeds through a short prefix and the complete state near
  source 79 without truncation or invalid structured output; and
* a fresh full-H0 Serial Native run completes with complete artifacts.

Any failure is recorded as deployment evidence. The clean method is not
modified to repair responses. A backend configuration change is allowed only
before the freeze and must create a new identity record.
