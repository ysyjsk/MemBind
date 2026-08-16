"""Authority-bound post-observation and finalization for a completed A0 run.

All controller, durable-attempt, and authority evidence is validated before
private environment loading or driver construction.  This module never grants
resume, cleanup, or pointer-update authority and contains no live call at
import time.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import os
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from .artifacts import payload_sha256
from .s5_a0_controller import (
    _default_env_file_loader,
    inspect_s5_a0_controller_attempt,
)
from .s5_a0_result_finalizer import (
    S5A0FinalizerPaths,
    finalize_s5_a0_result,
    verify_s5_a0_result,
)
from .s5_durable_attempt_store import inspect_s5_attempt
from .s5_live_authority import verify_s5_live_authority
from .s5_native_post_observation import (
    S5GraphitiPostQueryExecutor,
    observe_s5_native_post_namespace,
)


CHECKPOINT_SCHEMA = "membind.paper-eval-v3.s5-a0-postprocess-checkpoint.v1"
_PROJECT = Path(__file__).resolve().parents[2]
_ROOT = _PROJECT.parent
_LEGACY = _ROOT / "membind-validation"
_LEGACY_SRC = _LEGACY / "src"
_FLAGS = {
    "resume_authorized": False,
    "namespace_cleanup_authorized": False,
    "current_stage_pointer_update_authorized": False,
}
_CHECKPOINT_FIELDS = {
    "schema_version",
    "status",
    "failure_stage",
    "error_class",
    "post_observation_status",
    "final_result_status",
    "published_count",
    "last_published_source_sequence",
    *_FLAGS,
    "checkpoint_sha256",
}
_PRIVATE_FIELDS = {
    "api_key",
    "authorization",
    "body",
    "content",
    "credential",
    "episode",
    "group_id",
    "messages",
    "namespace",
    "password",
    "prompt",
    "raw_output",
    "raw_response",
    "request",
    "response",
    "run_id",
    "secret",
}


class S5A0PostprocessError(ValueError):
    """A postprocess prerequisite or durable terminal artifact failed closed."""


def _fail(code: str) -> S5A0PostprocessError:
    return S5A0PostprocessError(code)


def _assert_public(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or key.casefold() in _PRIVATE_FIELDS:
                raise _fail("private_postprocess_field")
            _assert_public(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _assert_public(child)


def _qualified_error_class(error: BaseException) -> str:
    return f"{type(error).__module__}.{type(error).__qualname__}"


def _load_json(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail(code) from None
    if not isinstance(value, dict):
        raise _fail(code)
    return value


def _write_exclusive(path: Path, value: Mapping[str, object]) -> None:
    selected = Path(path)
    selected.parent.mkdir(parents=True, exist_ok=True)
    data = (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("ascii")
    descriptor = os.open(selected, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o664)
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


def _checkpoint_path(paths: S5A0FinalizerPaths) -> Path:
    return Path(paths.controller_root).parent / "postprocess/checkpoint.json"


def _sealed_checkpoint(
    *,
    status: str,
    failure_stage: str | None,
    error_class: str | None,
    post_observation_status: str,
    final_result_status: str,
    published_count: int,
    last_published_source_sequence: int | None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": CHECKPOINT_SCHEMA,
        "status": status,
        "failure_stage": failure_stage,
        "error_class": error_class,
        "post_observation_status": post_observation_status,
        "final_result_status": final_result_status,
        "published_count": published_count,
        "last_published_source_sequence": last_published_source_sequence,
        **_FLAGS,
    }
    _assert_public(payload)
    payload["checkpoint_sha256"] = payload_sha256(payload)
    return payload


def inspect_s5_a0_postprocess_checkpoint(path: Path) -> dict[str, object]:
    """Verify one sanitized terminal postprocess checkpoint."""

    checkpoint = _load_json(path, "postprocess_checkpoint_invalid")
    seal = checkpoint.pop("checkpoint_sha256", None)
    if (
        set(checkpoint) | {"checkpoint_sha256"} != _CHECKPOINT_FIELDS
        or seal != payload_sha256(checkpoint)
        or checkpoint.get("schema_version") != CHECKPOINT_SCHEMA
        or checkpoint.get("status") not in {"complete", "incomplete_non_mergeable"}
        or checkpoint.get("resume_authorized") is not False
        or checkpoint.get("namespace_cleanup_authorized") is not False
        or checkpoint.get("current_stage_pointer_update_authorized") is not False
        or isinstance(checkpoint.get("published_count"), bool)
        or not isinstance(checkpoint.get("published_count"), int)
        or int(checkpoint["published_count"]) < 0
    ):
        raise _fail("postprocess_checkpoint_invalid")
    if checkpoint["status"] == "complete":
        if (
            checkpoint.get("failure_stage") is not None
            or checkpoint.get("error_class") is not None
            or checkpoint.get("post_observation_status") != "PASS"
            or checkpoint.get("final_result_status") != "PASS"
            or checkpoint.get("published_count") != 49
            or checkpoint.get("last_published_source_sequence") != 48
        ):
            raise _fail("postprocess_checkpoint_invalid")
    elif (
        not isinstance(checkpoint.get("failure_stage"), str)
        or not checkpoint.get("failure_stage")
        or not isinstance(checkpoint.get("error_class"), str)
        or not checkpoint.get("error_class")
    ):
        raise _fail("postprocess_checkpoint_invalid")
    checkpoint["checkpoint_sha256"] = seal
    _assert_public(checkpoint)
    return checkpoint


def _validate_prerequisites(
    paths: S5A0FinalizerPaths,
    *,
    git_commit: str,
) -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, object]]]:
    if not isinstance(paths, S5A0FinalizerPaths):
        raise _fail("finalizer_paths_invalid")
    if _checkpoint_path(paths).exists():
        raise _fail("postprocess_checkpoint_exists")
    if Path(paths.post_observation).exists():
        raise _fail("post_observation_exists")
    if Path(paths.result).exists():
        raise _fail("final_result_exists")
    try:
        controller = inspect_s5_a0_controller_attempt(paths.controller_root)
    except Exception:
        raise _fail("controller_prerequisite_invalid") from None
    checkpoint = controller["checkpoint"]
    events = controller["events"]
    if (
        checkpoint.get("status") != "controller_complete_evidence_only"
        or [event.get("event_type") for event in events]
        != [
            "authority_consumed",
            "runtime_constructed",
            "runtime_ready",
            "native_runner_started",
            "runtime_closed",
            "raw_runner_evidence_complete",
        ]
    ):
        raise _fail("controller_prerequisite_incomplete")
    try:
        attempt = inspect_s5_attempt(paths.attempt_root)
    except Exception:
        raise _fail("durable_attempt_prerequisite_invalid") from None
    manifest = attempt["manifest"]
    result = attempt.get("result")
    payload = result.get("payload") if isinstance(result, Mapping) else None
    source_sha256s = manifest.get("source_sha256s")
    if (
        manifest.get("method") != "A0"
        or not isinstance(source_sha256s, list)
        or len(source_sha256s) != 49
        or not isinstance(result, Mapping)
        or result.get("status") != "complete"
        or not isinstance(payload, Mapping)
        or payload.get("status") != "PASS"
    ):
        raise _fail("durable_attempt_prerequisite_incomplete")
    expected_sources = [
        {"source_sequence": index, "source_sha256": digest}
        for index, digest in enumerate(source_sha256s)
    ]
    publications = [
        deepcopy(dict(event))
        for event in attempt["events"]
        if event.get("event_type") == "publication"
    ]
    if (
        len(publications) != 49
        or [event.get("source_sequence") for event in publications] != list(range(49))
        or any(
            event.get("source_sha256") != expected_sources[index]["source_sha256"]
            for index, event in enumerate(publications)
        )
    ):
        raise _fail("durable_publication_prerequisite_invalid")
    try:
        authority = verify_s5_live_authority(
            _load_json(paths.authority, "authority_invalid")
        )
    except Exception:
        raise _fail("authority_prerequisite_invalid") from None
    run = authority["payload"].get("run")
    source_manifest = payload_sha256(expected_sources)
    if authority.get("git_commit") != git_commit:
        raise _fail("git_commit_binding_invalid")
    if (
        not isinstance(run, Mapping)
        or run.get("method") != "A0"
        or run.get("episode_count") != 49
        or run.get("source_manifest_sha256") != source_manifest
        or run.get("run_id") != checkpoint.get("run_id")
        or run.get("run_id") != manifest.get("run_id")
        or run.get("namespace") != f"pev3-{run.get('run_id')}"
    ):
        raise _fail("authority_prerequisite_binding_invalid")
    return dict(run), expected_sources, publications


async def _await(value: Awaitable[Any] | Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _failure_result(stage: str, error: BaseException) -> dict[str, object]:
    return {
        "status": "incomplete_non_mergeable",
        "failure_stage": stage,
        "error_class": _qualified_error_class(error),
        **_FLAGS,
    }


async def execute_s5_a0_postprocess(
    *,
    paths: S5A0FinalizerPaths,
    git_commit: str,
    env_loader: Callable[[], Mapping[str, str] | object],
    driver_factory: Callable[[object], Awaitable[object] | object],
    query_executor: Callable[..., Awaitable[Sequence[Mapping[str, object]]]]
    | None = None,
    finalizer: Callable[..., Awaitable[Mapping[str, object]] | Mapping[str, object]] = (
        finalize_s5_a0_result
    ),
) -> dict[str, object]:
    """Observe and finalize one already-complete A0 attempt, then stop."""

    if not isinstance(git_commit, str) or not git_commit:
        raise _fail("git_commit_invalid")
    if not callable(env_loader) or not callable(driver_factory) or not callable(finalizer):
        raise _fail("postprocess_dependency_invalid")
    run, expected_sources, publications = _validate_prerequisites(
        paths,
        git_commit=git_commit,
    )
    checkpoint_path = _checkpoint_path(paths)
    driver: object | None = None
    observation: Mapping[str, object] | None = None
    post_status = "NOT_AVAILABLE"
    final_status = "NOT_AVAILABLE"
    failure_stage = "environment_loading"
    failure: BaseException | None = None
    try:
        private_env = env_loader()
        failure_stage = "driver_construction"
        driver = await _await(driver_factory(private_env))
        failure_stage = "observation"
        selected_query = query_executor or S5GraphitiPostQueryExecutor(
            expected_sources=expected_sources
        )
        observation = await observe_s5_native_post_namespace(
            driver=driver,
            method="A0",
            run_id=str(run["run_id"]),
            namespace=str(run["namespace"]),
            expected_sources=expected_sources,
            durable_publication_events=publications,
            query_executor=selected_query,
        )
        post_status = str(observation.get("status", "INVALID_EVIDENCE"))
        failure_stage = "observation_persist"
        _write_exclusive(paths.post_observation, observation)
    except Exception as error:
        failure = error
    finally:
        if driver is not None:
            try:
                close = getattr(driver, "close")
                await _await(close())
            except Exception as close_error:
                if failure is None:
                    failure_stage = "driver_close"
                    failure = close_error
    if failure is None:
        failure_stage = "finalization"
        try:
            finalized = await _await(finalizer(paths=paths, git_commit=git_commit))
            verified = verify_s5_a0_result(finalized)
            persisted = verify_s5_a0_result(
                _load_json(paths.result, "final_result_missing")
            )
            if verified != persisted or persisted["payload"].get("verdict") != "PASS":
                raise _fail("final_result_binding_invalid")
            final_status = "PASS"
        except Exception as error:
            failure = error
    if failure is not None:
        checkpoint = _sealed_checkpoint(
            status="incomplete_non_mergeable",
            failure_stage=failure_stage,
            error_class=_qualified_error_class(failure),
            post_observation_status=post_status,
            final_result_status=final_status,
            published_count=49,
            last_published_source_sequence=48,
        )
        try:
            _write_exclusive(checkpoint_path, checkpoint)
        except (OSError, FileExistsError):
            raise _fail("postprocess_checkpoint_write_failed") from None
        inspect_s5_a0_postprocess_checkpoint(checkpoint_path)
        return _failure_result(failure_stage, failure)

    checkpoint = _sealed_checkpoint(
        status="complete",
        failure_stage=None,
        error_class=None,
        post_observation_status="PASS",
        final_result_status="PASS",
        published_count=49,
        last_published_source_sequence=48,
    )
    try:
        _write_exclusive(checkpoint_path, checkpoint)
    except (OSError, FileExistsError):
        raise _fail("postprocess_checkpoint_write_failed") from None
    inspect_s5_a0_postprocess_checkpoint(checkpoint_path)
    return {
        "status": "PASS",
        "method": "A0",
        "post_observation_status": "PASS",
        "final_result_status": "PASS",
        "published_count": 49,
        "last_published_source_sequence": 48,
        **_FLAGS,
    }


def _production_env(env_file: Path) -> Mapping[str, str]:
    return _default_env_file_loader(Path(env_file), _LEGACY_SRC)


def _production_driver(private_env: object) -> object:
    if not isinstance(private_env, Mapping):
        raise _fail("environment_invalid")
    uri = str(private_env.get("NEO4J_URI", ""))
    user = str(private_env.get("NEO4J_USER", ""))
    password = str(private_env.get("NEO4J_PASSWORD", ""))
    if uri != "bolt://localhost:7687" or not user or not password:
        raise _fail("neo4j_environment_invalid")
    from neo4j import AsyncGraphDatabase

    return AsyncGraphDatabase.driver(uri, auth=(user, password))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Observe and finalize one completed S5 Native A0 smoke"
    )
    parser.add_argument("--production-identity", type=Path, required=True)
    parser.add_argument(
        "--production-identity-qualification", type=Path, required=True
    )
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--authority", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
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
    run_root = Path(args.run_root)
    paths = S5A0FinalizerPaths(
        production_identity=args.production_identity,
        production_identity_qualification=args.production_identity_qualification,
        current_stage_pointer=args.current_stage_pointer,
        preflight=args.preflight,
        authority=args.authority,
        consumption=run_root / "authority_consumption.json",
        controller_root=run_root / "controller",
        attempt_root=run_root / "attempt",
        post_observation=run_root / "post_observation.json",
        result=run_root / "S5_A0_RESULT.json",
    )
    try:
        result = asyncio.run(
            execute_s5_a0_postprocess(
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
                ensure_ascii=True,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0 if result.get("status") == "PASS" else 2


__all__ = [
    "CHECKPOINT_SCHEMA",
    "S5A0PostprocessError",
    "build_parser",
    "execute_s5_a0_postprocess",
    "inspect_s5_a0_postprocess_checkpoint",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
