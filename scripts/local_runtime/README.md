# MemBind local Qwen runtime

This profile is isolated from the frozen 32B experiments. It places model
files, caches, logs, runtime state, profile metadata, and the Python
environment under `/data/predator/ly/Mem`.

## Layout

- GPU 0, `18100`: `Qwen3-14B-AWQ`, construction/Reader/Judge
- GPU 1, `18101`: `Qwen3-Embedding-0.6B`, pooling endpoint, 1024 dimensions
- tmux: `membind-local-llm` and `membind-local-embedding`
- Neo4j: existing `bolt://127.0.0.1:7687`
- Profile: `/data/predator/ly/Mem/profiles/local-qwen3-14b-awq-v1/profile.json`

## Commands

```bash
scripts/local_runtime/install_env.sh
scripts/local_runtime/download_models.sh
scripts/local_runtime/start_all.sh
source scripts/local_runtime/activate.sh
scripts/local_runtime/stop.sh
```

Inspect either live server console with:

```bash
tmux attach -t membind-local-llm
tmux attach -t membind-local-embedding
```

Detach without stopping a service by pressing `Ctrl-b`, then `d`.

For an unattended download, validation, startup, and preflight:

```bash
scripts/local_runtime/launch_background.sh
cat /data/predator/ly/Mem/run/membind-local/background-setup.status
tail -f /data/predator/ly/Mem/logs/membind-local/background-setup.log
```

`start_all.sh` starts both GPU services in persistent tmux sessions, waits for readiness, and runs the
structured-output plus 1024-dimensional embedding preflight. Logs and PID
files are written below `/data/predator/ly/Mem/logs/membind-local` and
`/data/predator/ly/Mem/run/membind-local`.

The local project clients should use:

```text
CONSTRUCTION_LLM_BASE_URL=http://127.0.0.1:18100/v1
CONSTRUCTION_LLM_MODEL=qwen3-14b-awq
EMBEDDING_BASE_URL=http://127.0.0.1:18101/v1
EMBEDDING_MODEL=qwen3-embedding-0.6b
EMBEDDING_DIM=1024
```

The local profile uses `membind-local` as a loopback-only API token. Source
`activate.sh` before running the project so Graphiti clients inherit the
local endpoints, model identities, cache paths, and Python path.

The LLM accepts up to 8 active sequences and uses an 8,192-token batching
budget. Its physical KV cache still limits a single 64K request to roughly
one resident sequence; shorter project requests can batch concurrently and
excess work queues under FCFS. Graphiti uses 8 logical construction
coroutines. The embedding server accepts up to 128 sequences with a
32,768-token batching budget.

The local Graphiti client uses a 32,768-token maximum completion budget. Real
MAB attempts at 2,048, 8,192, and 16,384 tokens ended with
`finish_reason=length` and deterministic JSON truncation at context 0 source
25; all timed attempts used for comparison must therefore start after the
32,768-token client
budget was activated and must record it in their runtime manifest.
For each request, the local runner applies the same Qwen chat template with
thinking disabled, counts the prompt locally, and caps only the wire output to
the remaining space inside the 65,536-token context with a 32-token safety
margin. A bounded six-attempt server-error fallback covers tokenizer drift.
Non-context provider errors are never retried by this adapter.

The construction transport uses a 3,600-second HTTP timeout and disables
OpenAI SDK retries. Context 0 source 25 produced a valid server-side response
after exceeding the SDK's 600-second default, so shorter client timeouts create
false infrastructure failures. Disabling hidden retries also keeps each
recorded transport attempt attributable to one Graphiti request.

The local Qwen Graphiti client also disables Graphiti's default four-attempt
tenacity wrapper. A deterministic JSON/context failure therefore records one
failed provider attempt instead of silently repeating a long request. This is
installed on the local client instance and does not modify the frozen 32B code.

When an extraction prompt cannot leave an 8,192-token completion inside the
65,536-token context, the local client partitions the current message at
complete `[USER]`/`[ASSISTANT]` turns, performs real extraction calls per partition,
and deterministically merges entities/edges. It never truncates a partition or
silently drops a returned item; the policy is recorded as
`dialogue_turn_partition_merge_v1`.

Do not overwrite the existing frozen client/backend manifests. A model or
provider swap requires a fresh model identity and a new experiment namespace.
Even though the local embedding dimension remains 1024, existing vectors
cannot be mixed with vectors from a different embedding model; rebuild the
Neo4j embeddings and vector indexes for a new campaign.
