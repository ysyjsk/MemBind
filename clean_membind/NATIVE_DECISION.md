# Native Decision

## Primary candidate

The clean mainline selects:

| Component | Frozen choice |
| --- | --- |
| Graph implementation | upstream `graphiti-core==0.29.3`, commit `021d3a57d511f21b10adaf7fa923bd5c1fce5e9d` |
| LLM client | Graphiti `OpenAIGenericClient` |
| Local runtime | Ollama OpenAI-compatible endpoint, `http://127.0.0.1:11434/v1` |
| LLM | `qwen2.5:14b` (open weights; exact Ollama digest must be recorded before freeze) |
| Embedding | Ollama `nomic-embed-text`, 768 dimensions |
| Structured output | `json_schema` first, because it is Graphiti's constrained-output path; no repair/retry algorithm is added |
| Database | existing Neo4j deployment, with URI/version recorded at freeze |
| GPU placement | one Ollama model server at a time during validation; two identical replicas only after resource measurement |

This is the only primary candidate. `deepseek-r1:7b`, the official local
example, is a documented backup for a deployment smoke test if qwen2.5 cannot
be obtained, not a second formal method or a model zoo.

## Why this candidate

1. It uses the official Graphiti client and untouched Graphiti extraction and
   deduplication code.
2. An independent open-source deployment actually uses `qwen2.5:14b` with
   Ollama and Nomic embeddings, and recommends roughly 16 GB VRAM.
3. A 14B model is a better fit for structured extraction than the smaller local
   models that previously produced truncation and invalid JSON in this project.
4. Ollama supplies a stable, inspectable OpenAI-compatible boundary; model
   digest, runtime version, and structured-output request are recorded as
   identity fields rather than inferred from a process name.

The evidence is sufficient to justify a minimal real validation, not to skip
it. The external project documents service operation and short smoke tests but
does not prove full LongMemEval-scale reliability.

## Freeze gate

The candidate becomes `NATIVE_READY_FOR_MAIN_EXPERIMENT` only after:

* the external deployment shape succeeds on this host;
* the real adapter succeeds through a short prefix and the complete state near
  source 79 without truncation or invalid structured output; and
* a fresh full-H0 Serial Native run completes with complete artifacts.

Any failure is recorded as deployment evidence. The clean method is not
modified to repair responses. A backend configuration change is allowed only
before the freeze and must create a new identity record.

