#!/usr/bin/env python3
"""Run one bounded production-v3.1 MEG OBSERVE_ONLY capture."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
REPOSITORY = PROJECT.parent
SOURCE = PROJECT / "src"
LEGACY = REPOSITORY / "membind-validation"
for position, path in enumerate((SOURCE, LEGACY / "src")):
    if str(path) not in sys.path:
        sys.path.insert(position, str(path))

from paper_eval.membind_v31.freezer import load_v31_state_cut_certification  # noqa: E402
from paper_eval.membind_v31.live_block import production_v31_live_hooks  # noqa: E402
from paper_eval.membind_v31.method_plan import verify_membind_v31_method_plan  # noqa: E402
from paper_eval.membind_v31.production_executor import (  # noqa: E402
    ProductionExecutorPaths,
    _default_env_loader,
    _default_episode_builder,
    load_development_episodes,
)
from paper_eval.membind_v4.mseg.graphiti_0293_audit import (  # noqa: E402
    audit_graphiti_0293,
)
from paper_eval.membind_v4.mseg.mutation_epoch import StateMutationEpoch  # noqa: E402
from paper_eval.membind_v4.mseg.runtime_instrumentation import (  # noqa: E402
    InstrumentationMode,
    MEGRuntimeRecorder,
    WriterDomainCertificate,
)
from paper_eval.membind_v4.mseg.runtime_live import (  # noqa: E402
    build_meg_observe_only_live_composition,
    build_observe_capture_contract,
    build_v31_observe_composition_proof,
    derive_observe_namespace,
    execute_meg_observe_capture,
)


DEFAULT_RUN_ID = "membind-v31-opt-w4-meg-runtime-observe-20260821-001"
HISTORY_ID = "07741c45"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--source-count", type=int, choices=(3, 12), default=3)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--repository-root", type=Path, default=REPOSITORY)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repository = Path(args.repository_root).resolve()
    paths = ProductionExecutorPaths.from_repository(repository)
    plan = verify_membind_v31_method_plan(
        json.loads(
            (paths.control_root / "V31_METHOD_PLAN.json").read_text(encoding="utf-8")
        )
    )
    run_id = str(args.run_id)
    output_root = (
        Path(args.output_root).resolve()
        if args.output_root is not None
        else paths.project_root
        / "artifacts/paper_eval/membind_v4/meg_runtime_instrumentation"
        / run_id
    )
    graphiti_root = (
        repository
        / "membind-validation/.venv/lib/python3.12/site-packages/graphiti_core"
    )
    audit = audit_graphiti_0293(graphiti_root)
    proof = build_v31_observe_composition_proof(
        project_root=paths.project_root,
        graphiti_source_hashes=audit["source_hashes"],
    )
    contract = build_observe_capture_contract(
        verified_plan=plan,
        run_id=run_id,
        output_root=output_root,
        source_count=args.source_count,
        composition_proof=proof,
    )
    builder = _default_episode_builder(paths.legacy_root)
    histories = load_development_episodes(
        development_input=paths.development_input,
        verified_plan=plan,
        episode_builder=builder,
    )
    episodes = tuple(histories[HISTORY_ID][: args.source_count])
    if len(episodes) != args.source_count:
        raise ValueError("meg_runtime_source_prefix_unavailable")
    print(
        json.dumps(
            {
                "status": "MEG_RUNTIME_OBSERVE_CAPTURE_PREFLIGHT_PASS",
                "run_id": run_id,
                "output_root": str(output_root),
                "namespace": contract["namespace"],
                "mode": "OBSERVE_ONLY",
                "history_id": HISTORY_ID,
                "source_sequences": contract["source_sequences"],
                "compile_workers": contract["compile_workers"],
                "lookahead": contract["lookahead"],
                "bind_workers": contract["bind_workers"],
                "global_llm_admission_k": contract["global_llm_admission_k"],
                "arrival_offsets_ns": contract["arrival_offsets_ns"],
                "saga_none": proof["saga_none"],
                "community_update_invoked": proof["community_update_invoked"],
                "shadow_reads": 0,
                "execution_policy_changed": False,
                "semantic_path_changed": False,
                "contract_payload_sha256": contract["payload_sha256"],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    if args.dry_run:
        return 0

    relevant_paths = tuple(
        str(row["path_id"])
        for row in audit["write_path_inventory"]
        if row["relevance"] == "RELEVANT_COVERED"
    )
    namespace = derive_observe_namespace(run_id)
    writer = WriterDomainCertificate.create(
        namespace=namespace,
        graph_backend="neo4j",
        authorized_writer_identity=run_id,
        write_path_coverage=relevant_paths,
        expected_write_paths=relevant_paths,
        external_writer_policy="DENY",
        commit_observer_coverage="ALL_MANAGED_COMMITS",
        fresh_namespace=True,
        no_background_mutation=True,
    )
    recorder = MEGRuntimeRecorder(
        mode=InstrumentationMode.OBSERVE_ONLY,
        writer_domain=writer,
    )
    mutation_epoch = StateMutationEpoch(
        namespace=namespace,
        backend_id="neo4j",
        epoch=f"{run_id}-epoch",
    )
    composition = build_meg_observe_only_live_composition(
        recorder=recorder,
        mutation_epoch=mutation_epoch,
        writer_domain=writer,
        stream_id=HISTORY_ID,
        base_hooks=production_v31_live_hooks(),
    )
    try:
        result = asyncio.run(
            execute_meg_observe_capture(
                contract=contract,
                episodes=episodes,
                env=_default_env_loader(paths.env_file),
                state_cut_certification=load_v31_state_cut_certification(
                    paths.freeze_paths
                ),
                composition=composition,
            )
        )
    except BaseException as error:
        print(
            json.dumps(
                {
                    "status": "STOP_REAL_RUNTIME_SEMANTIC_LINEAGE",
                    "error_class": f"{type(error).__module__}.{type(error).__qualname__}",
                    "error_code": str(error),
                    "output_root": str(output_root),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
            flush=True,
        )
        return 1
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0 if result["status"] == "PASS_REAL_MEG_RUNTIME_OBSERVE_ONLY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
