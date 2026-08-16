"""Independent Neo4j observation and terminal finalization for S5 M*.

The controller never calls this module.  It reopens only public, hash-sealed
execution evidence, observes Neo4j through a fresh driver, and cross-binds the
attempt ledger, publication journal, and materialized namespace before writing
one terminal scientific result.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import os
import sys
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .artifacts import payload_sha256, sha256_file
from .s5_a0_controller import _default_env_file_loader
from .s5_durable_attempt_store import inspect_s5_attempt
from .s5_live_authority import (
    verify_s5_live_authority,
    verify_s5_live_authority_consumption,
)
from .s5_live_preflight import verify_s5_live_preflight
from .s5_method_smoke_contract import (
    mstar_pipeline_to_smoke_records,
    validate_smoke_records,
)
from .s5_mstar_controller import inspect_s5_mstar_controller_attempt
from .s5_mstar_pipeline import (
    MStarSource,
    MStarSpec,
    verify_mstar_pipeline_evidence,
)
from .s5_mstar_post_observation import (
    build_s5_mstar_post_observation,
    verify_s5_mstar_post_observation,
)
from .s5_mstar_production_core_identity import (
    verify_s5_mstar_production_core_identity,
)
from .s5_mstar_production_runner import verify_s5_mstar_production_bindings
from .s5_mstar_publication_journal import S5MStarPublicationJournal
from .s5_mstar_result_finalizer import (
    finalize_s5_mstar_result,
    verify_s5_mstar_result,
)
from .s5_native_post_observation import (
    S5GraphitiPostQueryExecutor,
    observe_s5_native_post_namespace,
)
from .s5_production_identity_qualification import (
    bind_s5_production_identity_qualification,
    verify_s5_production_identity_qualification,
)
from .s5_production_runner import verify_s5_production_identity
from .s5_pstar_result_finalizer import verify_s5_pstar_result


CHECKPOINT_SCHEMA = "membind.paper-eval-v3.s5-mstar-postprocess-checkpoint.v1"
_PROJECT = Path(__file__).resolve().parents[2]
_LEGACY = _PROJECT.parent / "membind-validation"
_LEGACY_SRC = _LEGACY / "src"
_FLAGS = {
    "resume_authorized": False,
    "namespace_cleanup_authorized": False,
    "next_method_authorized": False,
    "current_stage_pointer_update_authorized": False,
}


class S5MStarPostprocessError(ValueError):
    """The completed M* evidence chain is incomplete or contradictory."""


def _fail(code: str) -> S5MStarPostprocessError:
    return S5MStarPostprocessError(code)


@dataclass(frozen=True)
class S5MStarPostprocessPaths:
    production_identity: Path
    production_identity_qualification: Path
    production_core_identity: Path
    fx0_qualification: Path
    current_stage_pointer: Path
    preflight: Path
    authority: Path
    predecessor: Path
    consumption: Path
    controller_root: Path
    attempt_root: Path
    post_observation: Path
    result: Path


def _load(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail(code) from None
    if not isinstance(value, dict):
        raise _fail(code)
    return value


def _exclusive(path: Path, value: Mapping[str, object]) -> None:
    selected = Path(path)
    selected.parent.mkdir(parents=True, exist_ok=True)
    data = (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("ascii")
    try:
        descriptor = os.open(selected, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o664)
    except FileExistsError:
        raise _fail("conflicting_existing_output") from None
    try:
        offset = 0
        while offset < len(data):
            offset += os.write(descriptor, data[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(selected.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def build_s5_mstar_smoke_summary(
    *,
    pipeline_evidence: Mapping[str, Any],
    expected_source_sequences: Sequence[int],
    direct_invariant_violation_count: int,
) -> dict[str, Any]:
    """Validate structural telemetry, then attach independent DB violations."""

    if (
        isinstance(direct_invariant_violation_count, bool)
        or not isinstance(direct_invariant_violation_count, int)
        or direct_invariant_violation_count < 0
    ):
        raise _fail("direct_invariant_violation_count_invalid")
    records = mstar_pipeline_to_smoke_records(pipeline_evidence)
    summary = validate_smoke_records(
        "M*",
        expected_source_sequences=expected_source_sequences,
        records=records,
    )
    # Pipeline telemetry has no authority to claim DB invariants.  The count is
    # attached only after the independent namespace observation is verified.
    summary["direct_invariant_violation_count"] = direct_invariant_violation_count
    return summary


def _source_inventory(manifest: Mapping[str, object]) -> list[dict[str, object]]:
    hashes = manifest.get("source_sha256s")
    if (
        not isinstance(hashes, list)
        or len(hashes) != 49
        or any(not isinstance(value, str) or len(value) != 64 for value in hashes)
    ):
        raise _fail("source_inventory_invalid")
    return [
        {"source_sequence": index, "source_sha256": digest}
        for index, digest in enumerate(hashes)
    ]


def _bindings(paths: S5MStarPostprocessPaths) -> dict[str, str]:
    concrete = {
        "production_identity_file_sha256": paths.production_identity,
        "production_core_identity_file_sha256": paths.production_core_identity,
        "fx0_qualification_file_sha256": paths.fx0_qualification,
        "production_identity_qualification_file_sha256": (
            paths.production_identity_qualification
        ),
        "current_stage_pointer_file_sha256": paths.current_stage_pointer,
        "preflight_file_sha256": paths.preflight,
        "authority_file_sha256": paths.authority,
        "predecessor_file_sha256": paths.predecessor,
        "consumption_file_sha256": paths.consumption,
        "controller_events_file_sha256": paths.controller_root / "events.jsonl",
        "controller_checkpoint_file_sha256": (
            paths.controller_root / "checkpoint.json"
        ),
        "attempt_manifest_file_sha256": paths.attempt_root / "manifest.json",
        "attempt_events_file_sha256": paths.attempt_root / "events.jsonl",
        "attempt_checkpoint_file_sha256": paths.attempt_root / "checkpoint.json",
        "attempt_result_file_sha256": paths.attempt_root / "result.json",
        "publication_journal_file_sha256": (
            paths.attempt_root / "publication_journal.jsonl"
        ),
        "post_observation_file_sha256": paths.post_observation,
    }
    result = {name: sha256_file(path) for name, path in concrete.items()}
    if any(value == "missing" for value in result.values()):
        raise _fail("binding_file_missing")
    return result


def _prerequisites(
    paths: S5MStarPostprocessPaths, git_commit: str
) -> dict[str, object]:
    try:
        identity = verify_s5_production_identity(
            _load(paths.production_identity, "production_identity_invalid")
        )
        qualification = verify_s5_production_identity_qualification(
            _load(
                paths.production_identity_qualification,
                "production_identity_qualification_invalid",
            )
        )
        qualification_binding = bind_s5_production_identity_qualification(
            qualification,
            file_sha256=sha256_file(paths.production_identity_qualification),
        )
        core = verify_s5_mstar_production_core_identity(
            _load(paths.production_core_identity, "production_core_identity_invalid")
        )
        fx0 = _load(paths.fx0_qualification, "fx0_qualification_invalid")
        preflight = verify_s5_live_preflight(
            _load(paths.preflight, "preflight_invalid")
        )
        authority = verify_s5_live_authority(
            _load(paths.authority, "authority_invalid")
        )
        predecessor = verify_s5_pstar_result(
            _load(paths.predecessor, "predecessor_invalid")
        )
        consumption = verify_s5_live_authority_consumption(
            _load(paths.consumption, "consumption_invalid")
        )
        controller = inspect_s5_mstar_controller_attempt(paths.controller_root)
        attempt = inspect_s5_attempt(paths.attempt_root)
        journal = S5MStarPublicationJournal.load(
            paths.attempt_root / "publication_journal.jsonl"
        )
    except Exception:
        raise _fail("execution_chain_invalid") from None

    pointer = _load(paths.current_stage_pointer, "pointer_invalid")
    pointer_payload = pointer.get("payload")
    run = authority["payload"].get("run")
    result = attempt.get("result")
    pipeline = result.get("payload") if isinstance(result, Mapping) else None
    if not isinstance(run, Mapping) or not isinstance(pipeline, Mapping):
        raise _fail("terminal_prerequisite_invalid")
    run_id = str(run.get("run_id", ""))
    sources = _source_inventory(attempt["manifest"])
    logical_times = {
        int(event["source_sequence"]): int(event["logical_time_ns"])
        for event in pipeline.get("events", [])
        if isinstance(event, Mapping) and event.get("event_type") == "intent"
    }
    if set(logical_times) != set(range(49)):
        raise _fail("logical_time_coverage_invalid")
    expected_sources = tuple(
        MStarSource(
            source_sequence=int(row["source_sequence"]),
            source_sha256=str(row["source_sha256"]),
            opaque_source=object(),
            logical_time_ns=logical_times[int(row["source_sequence"])],
        )
        for row in sources
    )
    spec = MStarSpec(
        run_id=run_id,
        production_core_identity_sha256=str(core["identity_sha256"]),
        prepare_concurrency=2,
    )
    try:
        verify_s5_mstar_production_bindings(
            spec=spec,
            identity=identity,
            production_core_identity=core,
            fx0_qualification=fx0,
        )
        verified_pipeline = verify_mstar_pipeline_evidence(
            pipeline,
            expected_spec=spec,
            expected_sources=expected_sources,
        )
    except Exception:
        raise _fail("pipeline_evidence_invalid") from None

    predecessor_payload = predecessor.get("payload")
    authority_predecessor = authority["payload"].get("predecessor")
    if (
        authority.get("git_commit") != git_commit
        or identity.get("method") != "M*"
        or qualification_binding.get("method") != "M*"
        or qualification_binding.get("production_identity_sha256")
        != identity.get("identity_sha256")
        or qualification_binding.get("production_identity_file_sha256")
        != sha256_file(paths.production_identity)
        or not isinstance(pointer_payload, Mapping)
        or pointer.get("payload_sha256") != payload_sha256(pointer_payload)
        or pointer_payload.get("current_stage") != "S3_CONFIGURATION_FROZEN"
        or qualification_binding.get("current_stage_pointer", {}).get("file_sha256")
        != sha256_file(paths.current_stage_pointer)
        or preflight["payload"].get("production_identity_qualification")
        != qualification_binding
        or authority["payload"].get("production_identity_qualification")
        != qualification_binding
        or authority["payload"].get("preflight_file_sha256")
        != sha256_file(paths.preflight)
        or authority["payload"].get("preflight_payload_sha256")
        != preflight.get("payload_sha256")
        or run.get("method") != "M*"
        or run.get("configured_concurrency") != 2
        or run.get("namespace") != f"pev3-{run_id}"
        or run.get("source_manifest_sha256") != payload_sha256(sources)
        or not isinstance(predecessor_payload, Mapping)
        or predecessor_payload.get("verdict") != "SCIENTIFIC_OUTCOME_COMPLETE"
        or not isinstance(authority_predecessor, Mapping)
        or authority_predecessor.get("result_file_sha256")
        != sha256_file(paths.predecessor)
        or authority_predecessor.get("result_payload_sha256")
        != predecessor.get("payload_sha256")
        or consumption["payload"].get("run") != run
        or consumption["payload"].get("authority_file_sha256")
        != sha256_file(paths.authority)
        or consumption["payload"].get("authority_payload_sha256")
        != authority.get("payload_sha256")
        or controller["checkpoint"].get("status")
        != "controller_complete_evidence_only"
        or controller["checkpoint"].get("run_id") != run_id
        or attempt["manifest"].get("run_id") != run_id
        or attempt["manifest"].get("method") != "M*"
        or attempt["manifest"].get("production_core_identity_sha256")
        != core.get("identity_sha256")
        or not isinstance(result, Mapping)
        or result.get("status") != "complete"
        or verified_pipeline.get("status") != "PASS"
    ):
        raise _fail("terminal_prerequisite_invalid")

    publications = [
        dict(event)
        for event in verified_pipeline["events"]
        if event.get("event_type") == "publication"
    ]
    if len(publications) != 49:
        raise _fail("publication_coverage_invalid")
    return {
        "run": dict(run),
        "identity": identity,
        "core": core,
        "predecessor": dict(authority_predecessor),
        "sources": sources,
        "pipeline": verified_pipeline,
        "publications": publications,
        "journal_events": list(journal.events),
    }


async def _await(value: Awaitable[Any] | Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def _default_observer(
    *,
    driver: object,
    run: Mapping[str, object],
    expected_sources: Sequence[Mapping[str, object]],
    publications: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    query = S5GraphitiPostQueryExecutor(expected_sources=expected_sources)
    return await observe_s5_native_post_namespace(
        driver=driver,
        method="M*",
        run_id=str(run["run_id"]),
        namespace=str(run["namespace"]),
        expected_sources=expected_sources,
        durable_publication_events=publications,
        query_executor=query,
    )


def _checkpoint_path(paths: S5MStarPostprocessPaths) -> Path:
    return paths.result.parent / "postprocess" / "checkpoint.json"


def _checkpoint(
    *, status: str, stage: str | None, error: BaseException | None, result: object
) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": CHECKPOINT_SCHEMA,
        "status": status,
        "failure_stage": stage,
        "error_class": (
            f"{type(error).__module__}.{type(error).__qualname__}"
            if error is not None
            else None
        ),
        "result_payload_sha256": (
            result.get("payload_sha256") if isinstance(result, Mapping) else None
        ),
        **_FLAGS,
    }
    value["checkpoint_sha256"] = payload_sha256(value)
    return value


async def execute_s5_mstar_postprocess(
    *,
    paths: S5MStarPostprocessPaths,
    git_commit: str,
    env_loader: Callable[[], Mapping[str, str]],
    driver_factory: Callable[[Mapping[str, str]], object],
    observer: Callable[..., object] = _default_observer,
) -> dict[str, object]:
    """Verify one complete attempt, observe Neo4j, and seal one result."""

    if not isinstance(paths, S5MStarPostprocessPaths):
        raise _fail("postprocess_paths_invalid")
    outputs = (paths.post_observation, paths.result, _checkpoint_path(paths))
    if any(Path(path).exists() for path in outputs):
        raise _fail("conflicting_existing_output")
    stage = "prerequisites"
    result_artifact: Mapping[str, object] | None = None
    driver: object | None = None
    try:
        chain = _prerequisites(paths, git_commit)
        run = chain["run"]
        stage = "environment_loading"
        env = env_loader()
        stage = "driver_construction"
        driver = await _await(driver_factory(env))
        stage = "native_observation"
        native = await _await(
            observer(
                driver=driver,
                run=run,
                expected_sources=chain["sources"],
                publications=chain["publications"],
            )
        )
        stage = "three_way_observation"
        post = build_s5_mstar_post_observation(
            run_id=str(run["run_id"]),
            expected_sources=chain["sources"],
            attempt_publications=chain["publications"],
            journal_events=chain["journal_events"],
            native_observation=native,
        )
        verify_s5_mstar_post_observation(post, expected_run_id=str(run["run_id"]))
        stage = "observation_persist"
        _exclusive(paths.post_observation, post)
        if driver is not None:
            stage = "driver_close"
            await _await(getattr(driver, "close")())
            driver = None

        stage = "final_projection"
        violations = int(post["summary"]["global_violation_total"])
        smoke = build_s5_mstar_smoke_summary(
            pipeline_evidence=chain["pipeline"],
            expected_source_sequences=list(range(49)),
            direct_invariant_violation_count=violations,
        )
        journal_counts = Counter(
            event["event_type"] for event in chain["journal_events"]
        )
        recovered = sum(
            event.get("event_type") == "publication"
            and event.get("recovered") is True
            for event in chain["journal_events"]
        )
        projection = {
            "run_id": run["run_id"],
            "execution_identity_sha256": payload_sha256(run),
            "source_manifest_sha256": payload_sha256(chain["sources"]),
            "production_identity_sha256": chain["identity"]["identity_sha256"],
            "production_core_identity_sha256": chain["core"]["identity_sha256"],
            "predecessor": chain["predecessor"],
            "smoke_summary": smoke,
            "post_observation": {
                "status": post["status"],
                "global_violation_total": violations,
                "native_observation_sha256": post["native_observation_sha256"],
                "post_observation_sha256": post["post_observation_sha256"],
            },
            "publication_journal": {
                "intent_count": journal_counts["intent"],
                "commit_count": journal_counts["commit"],
                "publication_count": journal_counts["publication"],
                "recovered_publication_count": recovered,
                "events_sha256": payload_sha256(chain["journal_events"]),
            },
            "bindings": _bindings(paths),
        }
        stage = "result_finalization"
        result_artifact = finalize_s5_mstar_result(
            output_path=paths.result,
            projection=projection,
            git_commit=git_commit,
        )
        verify_s5_mstar_result(result_artifact)
        success = _checkpoint(
            status="complete", stage=None, error=None, result=result_artifact
        )
        _exclusive(_checkpoint_path(paths), success)
        return {
            "status": "SCIENTIFIC_OUTCOME_COMPLETE",
            "method": "M*",
            "scientific_outcome": result_artifact["payload"]["scientific_outcome"],
            "scientific_pass": result_artifact["payload"]["verdict"] == "PASS",
            **_FLAGS,
        }
    except Exception as error:
        failure = _checkpoint(
            status="incomplete_non_mergeable",
            stage=stage,
            error=error,
            result=result_artifact,
        )
        _exclusive(_checkpoint_path(paths), failure)
        return {
            "status": "incomplete_non_mergeable",
            "failure_stage": stage,
            "error_class": failure["error_class"],
            **_FLAGS,
        }
    finally:
        if driver is not None:
            try:
                await _await(getattr(driver, "close")())
            except Exception:
                pass


def _production_env(path: Path) -> Mapping[str, str]:
    return _default_env_file_loader(Path(path), _LEGACY_SRC)


def _production_driver(env: Mapping[str, str]) -> object:
    from neo4j import AsyncGraphDatabase

    if not isinstance(env, Mapping) or env.get("NEO4J_URI") != "bolt://localhost:7687":
        raise _fail("neo4j_environment_invalid")
    return AsyncGraphDatabase.driver(
        env["NEO4J_URI"],
        auth=(env.get("NEO4J_USER"), env.get("NEO4J_PASSWORD")),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Observe and finalize S5 M*(C=2)")
    for name in (
        "production-identity",
        "production-identity-qualification",
        "production-core-identity",
        "fx0-qualification",
        "preflight",
        "authority",
        "predecessor",
        "run-root",
    ):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--git-commit", required=True)
    parser.add_argument(
        "--current-stage-pointer",
        type=Path,
        default=_PROJECT / "runtime/CURRENT_STAGE_STATUS.json",
    )
    parser.add_argument("--env-file", type=Path, default=_LEGACY / ".env")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.run_root)
    paths = S5MStarPostprocessPaths(
        production_identity=args.production_identity,
        production_identity_qualification=args.production_identity_qualification,
        production_core_identity=args.production_core_identity,
        fx0_qualification=args.fx0_qualification,
        current_stage_pointer=args.current_stage_pointer,
        preflight=args.preflight,
        authority=args.authority,
        predecessor=args.predecessor,
        consumption=root / "authority_consumption.json",
        controller_root=root / "controller",
        attempt_root=root / "attempt",
        post_observation=root / "post_observation.json",
        result=root / "S5_MSTAR_RESULT.json",
    )
    try:
        outcome = asyncio.run(
            execute_s5_mstar_postprocess(
                paths=paths,
                git_commit=str(args.git_commit),
                env_loader=lambda: _production_env(args.env_file),
                driver_factory=_production_driver,
            )
        )
    except Exception as error:
        print(
            json.dumps(
                {"status": "error", "error_class": type(error).__name__},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(outcome, sort_keys=True))
    return 0 if outcome.get("status") == "SCIENTIFIC_OUTCOME_COMPLETE" else 2


__all__ = [
    "CHECKPOINT_SCHEMA",
    "S5MStarPostprocessError",
    "S5MStarPostprocessPaths",
    "build_s5_mstar_smoke_summary",
    "execute_s5_mstar_postprocess",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
