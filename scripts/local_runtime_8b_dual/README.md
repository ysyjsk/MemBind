# MemBind Qwen3-8B resource-matched runtime

This directory defines a new experiment identity:
`local-qwen3-8b-awq-dualreplica-v1`. It does not modify or reuse the active
`local-qwen3-14b-awq-v1` service, ports, runtime state, logs, profiles, or
experiment output roots.

## Platform layout

| Physical GPU | Endpoint | Role | Initial reservation |
| --- | --- | --- | ---: |
| GPU 0 | `127.0.0.1:18200` | Qwen3-8B native replica | 0.90 |
| GPU 1 | `127.0.0.1:18201` | Qwen3-8B prepare replica | 0.70 |
| GPU 1 | `127.0.0.1:18202` | Qwen3-Embedding-0.6B | 0.25 |

Both LLM replicas use the same checkpoint, tokenizer, 65,536-token YaRN
context, 8 sequences, 8,192 batched tokens, FCFS, xgrammar, prefix caching,
chunked prefill, deterministic seed, and thinking-disabled chat template.
The embedding service uses BF16, 32,768 context, 128 sequences, and a
32,768-token batch limit.

The GPU 1 split is a deployment candidate, not a claimed optimal point.
`start_all.sh` admits it only if live logs prove that the prepare replica has
at least 65,536 KV tokens and the embedding process has at least 32,768 KV
tokens. Failure leaves the profile `FAILED`; it never silently shortens a
request or changes one method's budget.

The two 3090 Ti cards communicate through `PHB`, not NVLink. Qwen3-8B fits on
one card, so the main platform uses two replicas instead of TP=2. This avoids
per-layer PCIe collectives and provides the isolation needed to overlap
PREPARE work with authoritative NATIVE work.

## Fair comparison matrix

| Comparison | Native | V6.1 | Interpretation |
| --- | --- | --- | --- |
| Headline dual replica | Both 8B endpoints; capacity-weighted least-outstanding, phase-blind, work-conserving | Same two endpoints; PREPARE -> GPU 1, NATIVE -> GPU 0 | Measures semantic DAG-aware placement |
| Static-role strong baseline | Same two endpoints; Graphiti extraction requests -> GPU 1, other requests -> GPU 0; no capture/replay | Compared with V6.1 on the same platform | Separates simple request-class placement from certified semantic reuse |
| Single-GPU ablation | GPU 0 endpoint only | Same GPU 0 endpoint only | Measures scheduling/code changes without an extra replica |
| Cross-model context | Frozen 14B result | Fresh 8B result | Descriptive only; never reported as method speedup |

The Native headline baseline is deliberately allowed to use both replicas.
Giving only V6.1 a second GPU would confound method speedup with resource
count. Conversely, V6.1's phase affinity is the system mechanism under test,
so the phase-blind Native router remains work-conserving but cannot inspect
MemBind phase labels.

Changing the LLM requires a fresh Native8B baseline, fresh namespaces, and
fresh embeddings/vector indexes. A frozen Native14B run cannot be relabeled
as the 8B baseline even when the workload is otherwise identical.

## Safety and startup

The normal startup preflight intentionally fails while the current 14B GPU
processes and legacy ports are active. It reports the conflict but never stops
them. Switch profiles only after explicitly deciding that no 14B experiment
is running:

```bash
scripts/local_runtime_8b_dual/preflight.sh --mode static
scripts/local_runtime_8b_dual/preflight.sh --mode startup

# This is an explicit operator action, not performed by the 8B platform.
scripts/local_runtime/stop.sh

scripts/local_runtime_8b_dual/start_all.sh
source scripts/local_runtime_8b_dual/activate.sh
scripts/local_runtime_8b_dual/status.sh
```

To inspect commands without starting anything:

```bash
scripts/local_runtime_8b_dual/start_all.sh --dry-run
```

For background startup:

```bash
scripts/local_runtime_8b_dual/launch_background.sh
tail -f /data/predator/ly/Mem/logs/local-qwen3-8b-awq-dualreplica-v1/background-setup.log
cat /data/predator/ly/Mem/run/local-qwen3-8b-awq-dualreplica-v1/background-setup.status
```

The profile is experiment-eligible only when the status line is `READY` and
names an immutable `platform_manifest.*.json` plus its payload SHA-256.

## Experiment identity

Create a run contract before each measured attempt. The output must remain
inside `/data/predator/ly/Mem/experiments/local-qwen3-8b-awq-dualreplica-v1`:

```bash
source scripts/local_runtime_8b_dual/activate.sh

python scripts/local_runtime_8b_dual/make_experiment_manifest.py \
  --arm native-dual \
  --run-id mab-history0-native8b \
  --namespace local-qwen3-8b-awq-dualreplica-v1-mab-h0-native-001 \
  --platform-manifest /data/predator/ly/Mem/profiles/local-qwen3-8b-awq-dualreplica-v1/platform_manifest.TIMESTAMP.HASH.json \
  --workload-manifest /path/to/frozen-workload.json \
  --runner-implementation /path/to/native-dual-router.py \
  --output "$MEMBIND_EXPERIMENT_ROOT/mab-history0-native8b/run_contract.json"
```

Create the V6.1 contract with `--arm v61-dual`, then check resource matching:

```bash
scripts/local_runtime_8b_dual/fairness_check.sh \
  --native /path/to/native/run_contract.json \
  --v61 /path/to/v61/run_contract.json
```

The check requires the same platform hash, workload hash, endpoint set,
embedding identity, and decoding contract. Only method implementation,
routing policy, phase visibility/binding, namespace, and run identity may
differ.

## Review-facing reporting rules

Report construction makespan, throughput, time-to-first-publication, p50/p95
source latency, provider attempts, prompt/output tokens, work amplification,
GPU utilization, KV occupancy, DB writes, and semantic correctness. Run
Native and V6.1 in a randomized or Latin-square order with cooldown, record
at least three independent repetitions per condition, and report confidence
intervals rather than a single warm-cache number.

Warmup must be symmetric. Prefix-cache state must be cold-reset for every
measured repetition or deterministically primed with the same unmeasured
workload. Run only one compared arm at a time on the platform. Background GPU
processes, clocks/power limits, vLLM version, checkpoint hash, prompt/token
budget, database state, and embedding/index state must be captured in each
run contract or linked platform manifest.

The system rationale follows the established design space represented by
DistServe (phase disaggregation), Splitwise (phase-aware resource placement),
Sarathi-Serve (chunked prefill and interference control), Parrot (application
DAG-aware scheduling), Llumnix (multi-instance isolation/rescheduling), and
AlpaServe (replica/parallelism resource tradeoffs). MemBind's distinguishing
handoff is semantic: an exact extraction transcript crosses phases; model KV
does not.
