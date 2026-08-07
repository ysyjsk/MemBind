# MemBind Validation

This directory implements the pilot protocol in ../MemBind_basic_validation_experiment.md.

The code is organized around testable protocol invariants first, then live Graphiti adapters:

- src/dataset.py: frozen split and LongMemEval-S episode rendering.
- src/semantic_compile.py: evidence fence and unbound compile artifacts.
- src/latest_state_bind.py: source-ordered exactly-once publish.
- src/graphiti_native.py: M0 serial and M1 whole-update parallel runners.
- src/graphiti_membind.py: M2 Graphiti split using Graphiti v0.29.3 internal extraction and resolution boundaries.
- src/replay_driver.py: environment gate, split, run-plan, calibration/run/analyze CLI.

The local .env file contains the requested endpoints and API key. It is excluded
by .gitignore. Load it before live runs:

    set -a
    . .env
    set +a

Local setup:

    python -m venv .venv
    . .venv/bin/activate
    python -m pip install -e .
    python -m unittest discover -s tests

Local Neo4j without Docker:

    bash scripts/install_local_neo4j.sh
    bash scripts/start_local_neo4j.sh

Remote model contract smoke:

    python src/replay_driver.py gate --structured-checks 20

Typical protocol sequence:

    .venv/bin/python src/replay_driver.py split --data /path/to/longmemeval_s_cleaned.json
    .venv/bin/python src/replay_driver.py gate --structured-checks 20
    .venv/bin/python src/replay_driver.py integration
    .venv/bin/python src/replay_driver.py smoke --data /path/to/longmemeval_s_cleaned.json --attempt smoke03
    .venv/bin/python src/replay_driver.py calibrate --data /path/to/longmemeval_s_cleaned.json --arrival-interval-ms 0 --attempt calibration01
    .venv/bin/python src/replay_driver.py plan --attempt formal01
    .venv/bin/python src/replay_driver.py execute --data /path/to/longmemeval_s_cleaned.json
    .venv/bin/python src/replay_driver.py analyze --bootstrap-samples 10000

Notes for this machine:

- The user authorized local RTX 3090 execution instead of the protocol's dual RTX PRO 6000 hardware.
- Model services are remote and shared by M0, M1, and M2; only Neo4j is local.
- The 2048-token structured-output budget is tried first. A parse-truncated JSON
  response gets one shared, bounded 8192-token retry; both attempts are metered.
- Because every call extracts one current episode, the constrained JSON schema
  freezes `episode_indices` to `[0]`; the same schema is part of cache hashing.
- Neo4j edge score queries feeding RRF use
  `logical_content_ascending_before_top_k`: equal scores are ordered by
  UUID-independent fact, relation, temporal fields, and endpoint names before
  the outer database `LIMIT`. The full-text procedure's own internal cutoff is
  a retained residual risk and must be checked by the next correctness smoke.
- Neo4j node score queries use
  `logical_node_content_ascending_before_top_k`: equal cosine scores are
  ordered by UUID-independent name, summary, and labels before `LIMIT`. This
  stabilizes the per-entity candidate membership consumed by node dedupe.
- Graphiti RRF still selects the edge-resolution top-K candidate set. Before
  candidates receive prompt indices, all methods canonically present that
  selected set using `logical_content_ascending_after_top_k`; associated scores
  move with their edges. This removes database/UUID ordering drift without
  changing candidate membership.
- Node semantic search still selects the node-resolution candidate set. All
  methods apply `logical_content_ascending_before_candidate_id` to the merged
  set before Graphiti assigns candidate IDs; the prompt and ID-to-node mapping
  therefore consume the same UUID-independent logical order.
- A vLLM context-budget 400 triggers a one-token exact-usage probe, then retries
  within the true remainder minus 32 tokens; input prompts are never clipped.
- The construction runtime is frozen at the user-approved vLLM 0.26.0 with
  `max_model_len>=40960`. The earlier `smoke06` and
  `diagnostic_context_cap_005` failures under a 32768-token service are retained
  as evidence; the environment gate resolves their blocker only after the live
  endpoint satisfies the new frozen contract. See
  `artifacts/environment/construction_context_blocker.json`.
- Failed smoke and formal runs are retained. Replacement attempts always use a
  new run id.
- A read-only replay miss persists component hashes, the requested PromptParts,
  and the nearest frozen cache record under `artifacts/unexpected_prompts/`;
  this diagnostic never contains service API keys.
- Calibration and formal plan attempts are explicit. Calibration provenance is
  stored under `artifacts/calibration/attempts/`, and immutable formal plan
  snapshots are stored under `artifacts/plans/`; use a new attempt id to rerun.
- The complete optimized protocol is in EXPERIMENT_PLAN.md.
- If graphiti-core is not installed, the unit tests still validate the protocol logic; live runs require the venv install step.
