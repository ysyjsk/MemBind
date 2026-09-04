#!/usr/bin/env python3
"""Resume QA on an already sealed MAB v1.3 construction namespace.

This entry point never reconstructs a graph.  It validates the immutable
construction seal and appends read-only QA evidence to the block's QA lane.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
SFWB = ROOT / "saturated_fixed_work_baseline_v1_3"
VALIDATION = ROOT / "membind-validation"
PAPER = ROOT / "paper-eval-v3"
MAB = ROOT / "mab_quality_v2_final_qa"
for source in (SFWB / "src", VALIDATION / "src", PAPER / "src", MAB / "src"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))
if str(SFWB / "scripts") not in sys.path:
    sys.path.insert(0, str(SFWB / "scripts"))

from mab_quality_v2_final_qa.mab8192_adapter import (  # noqa: E402
    MAB8192_ADAPTER_VERSION,
    MAB8192Manifest,
)
from mab_quality_v2_final_qa.mab_main_dataset import build_authority  # noqa: E402
from saturated_fixed_work_baseline_v1_3.artifact_seals import verify_seal  # noqa: E402
from run_mab_v13_live import _run_qa  # noqa: E402


FORMAL_UPSTREAM_ARMS = frozenset(
    {
        "GRAPHITI_SERIAL_UPSTREAM_CORE_MAB8192",
        "MEMBIND_V6_1_UPSTREAM_CORE_MAB8192",
        "GRAPHITI_ASYNC_UPSTREAM_CORE_MAB8192",
    }
)


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _append(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _validate_target(
    *,
    block_root: Path,
    frozen_authority: Mapping[str, Any],
    authority: Mapping[str, Any],
) -> tuple[dict[str, Any], Any, MAB8192Manifest]:
    """Validate the block identity and return its seal plus official context."""

    verify_seal(block_root)
    seal = _json(block_root / "construction_seal.json")
    identity = seal.get("identity")
    if not isinstance(identity, Mapping):
        raise RuntimeError("CONSTRUCTION_SEAL_IDENTITY_INVALID")
    authority_public = {key: value for key, value in authority.items() if key != "contexts"}
    if identity.get("dataset_authority_sha256") != authority_public.get("authority_sha256"):
        raise RuntimeError("AUTHORITY_HASH_MISMATCH")
    if identity.get("dataset_authority_sha256") != frozen_authority.get("authority_sha256"):
        raise RuntimeError("FROZEN_AUTHORITY_HASH_MISMATCH")
    if identity.get("method") not in FORMAL_UPSTREAM_ARMS:
        raise RuntimeError("METHOD_NOT_FROZEN")
    if not isinstance(identity.get("namespace"), str) or not identity["namespace"]:
        raise RuntimeError("NAMESPACE_IDENTITY_INVALID")
    context = next(
        (item for item in authority["contexts"] if item.context_id == identity.get("context_id")),
        None,
    )
    if context is None:
        raise RuntimeError("CONTEXT_IDENTITY_INVALID")
    manifest = MAB8192Manifest.from_context(
        context, dataset_revision=str(authority_public["revision"])
    )
    if manifest.manifest_sha256 != identity.get("workload_hash"):
        raise RuntimeError("WORKLOAD_HASH_MISMATCH")
    adapter_coverage = _json(block_root / "adapter_coverage.json")
    if (
        adapter_coverage.get("status") != "PASS"
        or adapter_coverage.get("adapter_version") != MAB8192_ADAPTER_VERSION
        or adapter_coverage.get("chunk_count") != len(manifest.chunks)
        or adapter_coverage.get("session_count") != len(context.sessions)
    ):
        raise RuntimeError("MAB8192_ADAPTER_COVERAGE_MISMATCH")
    return seal, context, manifest


def _qa_episode_provenance(manifest: MAB8192Manifest) -> tuple[Any, ...]:
    return tuple(
        SimpleNamespace(
            source_sequence=chunk.global_sequence,
            session_id=chunk.session_id,
        )
        for chunk in manifest.chunks
    )


async def _main(args: argparse.Namespace) -> int:
    block_root = args.block_root.resolve()
    frozen_root = args.frozen_root.resolve()
    frozen_authority = _json(frozen_root / "dataset_authority.json")
    authority = build_authority(ROOT / "mab_quality_v2_final_qa" / "data" / "official_5_contexts.json")
    seal, context, manifest = _validate_target(
        block_root=block_root,
        frozen_authority=frozen_authority,
        authority=authority,
    )
    identity = seal["identity"]
    flat_seal = {
        "status": "CONSTRUCTION_SEALED",
        "context_id": identity["context_id"],
        "method": identity["method"],
        "namespace": identity["namespace"],
        "workload_hash": identity["workload_hash"],
    }

    def runtime_builder():
        if args.qa_runtime is None:
            raise RuntimeError("READ_ONLY_QA_RUNTIME_REQUIRED")
        return args.qa_runtime

    qa_output_root = (args.qa_output_root or (block_root / "qa")).resolve()
    ledger = block_root.parent / "qa_ledger.jsonl"
    start = {
        "event": "QA_RESUME_START",
        "run_id": identity.get("run_id"),
        "context_id": identity["context_id"],
        "context_index": next(i for i, item in enumerate(authority["contexts"]) if item.context_id == context.context_id),
        "method": identity["method"],
        "namespace": identity["namespace"],
        "block_root": str(block_root),
        "qa_scope": args.scope,
        "started_at_ns": time.monotonic_ns(),
    }
    _append(ledger, start)
    try:
        summaries: dict[str, Any] = {}
        scopes = ("SMOKE", "FULL") if args.scope == "BOTH" else (args.scope,)
        for scope in scopes:
            summary = await _run_qa(
                block_root=block_root,
                construction_seal=flat_seal,
                context=context,
                qa_scope=scope,
                runtime_builder=runtime_builder,
                qa_runtime=args.qa_runtime,
                qa_output_root=qa_output_root,
                episode_provenance=_qa_episode_provenance(manifest),
            )
            summaries[scope.lower()] = summary
            (block_root / f"qa_{scope.lower()}_summary.json").write_text(
                json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
        final = summaries[scopes[-1].lower()]
        _append(
            ledger,
            {
                **start,
                "event": "QA_RESUME_COMPLETE",
                "status": "PASS" if final.get("quality_status") in {"PASS", "PASS_WITH_INVALID_ROWS"} else "INVALID",
                "quality_status": final.get("quality_status"),
                "completed_count": final.get("completed_count"),
                "invalid_count": final.get("invalid_count"),
                "ended_at_ns": time.monotonic_ns(),
            },
        )
        return 0
    except Exception as exc:
        failure = {
            "status": "FAILED",
            "scope": args.scope,
            "error_type": f"{type(exc).__module__}.{type(exc).__qualname__}",
            "error": str(exc)[:500],
            "context_id": identity["context_id"],
            "method": identity["method"],
            "namespace": identity["namespace"],
        }
        (block_root / "qa_resume_failure.json").write_text(
            json.dumps(failure, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        _append(ledger, {**start, "event": "QA_RESUME_FAILURE", **failure, "ended_at_ns": time.monotonic_ns()})
        print(json.dumps(failure, ensure_ascii=False, sort_keys=True))
        return 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frozen-root", type=Path, required=True)
    parser.add_argument("--block-root", type=Path, required=True)
    parser.add_argument("--scope", choices=("SMOKE", "FULL", "BOTH"), default="BOTH")
    parser.add_argument("--qa-output-root", type=Path)
    args = parser.parse_args()
    args.qa_runtime = None
    try:
        if os.environ.get("MAB_RUNTIME_PROVIDER") == "LOCAL_DUAL_REPLICA":
            # The generic quality runtime defaults to the historical 32B
            # deployment. Formal local QA binds the same read-only graph-quality
            # implementation to the authenticated dual-replica profile.
            from paper_eval import graph_quality_live
            graph_quality_live.NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://127.0.0.1:7687")
            graph_quality_live.EMBEDDING_BASE_URL = os.environ.get("EMBEDDING_BASE_URL", "http://127.0.0.1:18202/v1").rstrip("/")
            graph_quality_live.EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "qwen3-embedding-0.6b")
            graph_quality_live.EMBEDDING_DIMENSION = int(os.environ.get("EMBEDDING_DIM", "1024"))
        from graphiti_native import load_env_file
        from paper_eval.graph_quality_live import build_graph_quality_runtime

        env: dict[str, str] = {}
        env.update(load_env_file(VALIDATION / ".env"))
        env.update(os.environ)
        args.qa_runtime = build_graph_quality_runtime(env=env)
        return asyncio.run(_main(args))
    except Exception as exc:
        print(json.dumps({"status": "FAILED", "error_type": f"{type(exc).__module__}.{type(exc).__qualname__}", "error": str(exc)[:500]}, ensure_ascii=False, sort_keys=True))
        return 2
    finally:
        if args.qa_runtime is not None:
            try:
                asyncio.run(args.qa_runtime.aclose())
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
