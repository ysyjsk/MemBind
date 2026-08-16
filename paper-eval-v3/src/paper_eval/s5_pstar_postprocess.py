"""Authority-bound bounded observation and finalization for P*(C=2)."""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import os
import sys
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from datetime import datetime

from .artifacts import payload_sha256
from .s5_a0_controller import _default_env_file_loader
from .s5_durable_attempt_store import inspect_s5_attempt
from .s5_live_authority import verify_s5_live_authority
from .s5_native_post_observation import (
    ENTITY_OBSERVATION, EPISODIC_OBSERVATION, RELATES_TO_OBSERVATION,
    S5GraphitiPostQueryExecutor,
)
from .s5_pstar_controller import inspect_s5_pstar_controller_attempt
from .s5_pstar_post_observation import (
    build_s5_pstar_post_observation, verify_s5_pstar_post_observation,
)
from .s5_pstar_result_finalizer import (
    S5PStarFinalizerPaths, finalize_s5_pstar_result, verify_s5_pstar_result,
)

CHECKPOINT_SCHEMA = "membind.paper-eval-v3.s5-pstar-postprocess-checkpoint.v1"
_PROJECT = Path(__file__).resolve().parents[2]
_LEGACY = _PROJECT.parent / "membind-validation"
_LEGACY_SRC = _LEGACY / "src"
_FLAGS = {"resume_authorized": False, "namespace_cleanup_authorized": False, "current_stage_pointer_update_authorized": False}


class S5PStarPostprocessError(ValueError):
    pass


def _fail(code): return S5PStarPostprocessError(code)


def _load(path, code):
    try: value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError): raise _fail(code) from None
    if not isinstance(value, dict): raise _fail(code)
    return value


def _exclusive(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode("ascii")
    try: fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o664)
    except FileExistsError: raise _fail("conflicting_existing_output") from None
    try: os.write(fd, data); os.fsync(fd)
    finally: os.close(fd)


def _checkpoint_path(paths): return Path(paths.result).parent / "postprocess/checkpoint.json"


def _seal_checkpoint(*, status, failure_stage, error_class, post, result_payload_sha256, accounting):
    value = {
        "schema_version": CHECKPOINT_SCHEMA, "status": status,
        "failure_stage": failure_stage, "error_class": error_class,
        "post_observation_status": post.get("status") if post else "NOT_AVAILABLE",
        "post_observation_sha256": post.get("observation_sha256") if post else None,
        "result_payload_sha256": result_payload_sha256,
        "terminal_accounting": accounting, **_FLAGS,
    }
    value["checkpoint_sha256"] = payload_sha256(value)
    return value


def inspect_s5_pstar_postprocess_checkpoint(path):
    value = _load(path, "postprocess_checkpoint_invalid"); seal = value.pop("checkpoint_sha256", None)
    accounting = value.get("terminal_accounting")
    if (seal != payload_sha256(value) or value.get("schema_version") != CHECKPOINT_SCHEMA
        or value.get("status") not in {"complete", "incomplete_non_mergeable"}
        or any(value.get(k) is not False for k in _FLAGS)
        or not isinstance(accounting, Mapping)
        or sum(accounting.get(k, -100) for k in ("published", "failed", "censored")) != 49):
        raise _fail("postprocess_checkpoint_invalid")
    if value["status"] == "complete" and (value.get("failure_stage") is not None or value.get("error_class") is not None): raise _fail("postprocess_checkpoint_invalid")
    value["checkpoint_sha256"] = seal; return value


def _prerequisites(paths, git_commit):
    controller = inspect_s5_pstar_controller_attempt(paths.controller_root)
    attempt = inspect_s5_attempt(paths.attempt_root)
    authority = verify_s5_live_authority(_load(paths.authority, "authority_invalid"))
    run = authority["payload"]["run"]
    result = attempt.get("result"); payload = result.get("payload") if isinstance(result, Mapping) else None
    if (authority.get("git_commit") != git_commit or run.get("method") != "P*"
        or controller["checkpoint"].get("status") != "controller_complete_evidence_only"
        or attempt["manifest"].get("run_id") != run.get("run_id")
        or result.get("status") not in {"complete", "scientific_outcome_complete"}
        or not isinstance(payload, Mapping)):
        raise _fail("terminal_prerequisite_invalid")
    hashes = attempt["manifest"]["source_sha256s"]
    expected = [{"source_sequence": i, "source_sha256": digest} for i, digest in enumerate(hashes)]
    terminals = [{k: event[k] for k in ("source_sequence", "source_sha256", "terminal_classification")}
                 for event in attempt["events"] if event.get("event_type") == "source_terminal"]
    if len(expected) != 49 or len(terminals) != 49: raise _fail("terminal_accounting_invalid")
    accounting = Counter(row["terminal_classification"] for row in terminals)
    counts = {"expected": 49, "published": accounting["PUBLISHED"], "failed": accounting["TREATMENT_FAILED"], "censored": accounting["CENSORED_NOT_STARTED_AFTER_TREATMENT_FAILURE"]}
    return run, expected, terminals, counts


async def _await(value): return await value if inspect.isawaitable(value) else value


async def _default_observer(*, driver, run, expected_sources, source_terminals):
    query = S5GraphitiPostQueryExecutor(expected_sources=expected_sources)
    episodes = await query(driver, EPISODIC_OBSERVATION, str(run["namespace"]))
    entities = await query(driver, ENTITY_OBSERVATION, str(run["namespace"]))
    relations = await query(driver, RELATES_TO_OBSERVATION, str(run["namespace"]))
    observed = [{"source_sequence": row["source_sequence"], "source_sha256": row["source_sha256"]} for row in episodes]
    entity_ids = {row["record_id"] for row in entities if row.get("group_id") == run["namespace"]}
    violation = Counter(); per_source = Counter()
    episode_source = {row["record_id"]: int(row["source_sequence"]) for row in episodes}
    violation["entity_namespace_escape_count"] = sum(row.get("group_id") != run["namespace"] for row in entities)
    for row in relations:
        provenance = row.get("provenance", [])
        attributable = {episode_source[p.get("episode_id")] for p in provenance if p.get("episode_id") in episode_source}
        count = 0
        if row.get("group_id") != run["namespace"]:
            violation["relation_namespace_escape_count"] += 1; count += 1
        for endpoint in ("source_entity_id", "target_entity_id"):
            if row.get(endpoint) not in entity_ids:
                violation["endpoint_escape_count"] += 1; count += 1
        if not provenance:
            violation["provenance_dangling_count"] += 1; count += 1
        for item in provenance:
            if item.get("exists") is not True:
                violation["provenance_dangling_count"] += 1; count += 1
            elif item.get("group_id") != run["namespace"]:
                violation["provenance_cross_namespace_count"] += 1; count += 1
        valid, invalid = row.get("valid_at"), row.get("invalid_at")
        if valid is not None and invalid is not None:
            try:
                valid_time = valid if isinstance(valid, datetime) else datetime.fromisoformat(str(valid).replace("Z", "+00:00"))
                invalid_time = invalid if isinstance(invalid, datetime) else datetime.fromisoformat(str(invalid).replace("Z", "+00:00"))
            except ValueError:
                raise _fail("temporal_observation_invalid") from None
            if invalid_time < valid_time:
                violation["valid_invalid_reversal_count"] += 1; count += 1
        for source in attributable: per_source[str(source)] += count
    published = [row["source_sequence"] for row in source_terminals if row["terminal_classification"] == "PUBLISHED"]
    return build_s5_pstar_post_observation(
        run_id=str(run["run_id"]), expected_sources=expected_sources,
        source_terminals=source_terminals, observed_episodics=observed,
        violation_counts=dict(violation),
        per_source_violation_counts={str(i): per_source[str(i)] for i in published},
    )


def _idempotent(paths):
    checkpoint_path = _checkpoint_path(paths)
    existing = [checkpoint_path.exists(), Path(paths.post_observation).exists(), Path(paths.result).exists()]
    if not any(existing): return None
    if not all(existing): raise _fail("conflicting_existing_output")
    try:
        checkpoint = inspect_s5_pstar_postprocess_checkpoint(checkpoint_path)
        post = verify_s5_pstar_post_observation(_load(paths.post_observation, "existing_post_invalid"))
        result = verify_s5_pstar_result(_load(paths.result, "existing_result_invalid"))
    except Exception: raise _fail("conflicting_existing_output") from None
    if (checkpoint["status"] != "complete" or checkpoint["post_observation_sha256"] != post["observation_sha256"]
        or checkpoint["result_payload_sha256"] != result["payload_sha256"]
        or checkpoint["terminal_accounting"] != result["payload"]["terminal_accounting"]):
        raise _fail("conflicting_existing_output")
    return {"status": "SCIENTIFIC_OUTCOME_COMPLETE", "method": "P*", "post_observation_status": post["status"], "terminal_accounting": checkpoint["terminal_accounting"], **_FLAGS}


async def execute_s5_pstar_postprocess(*, paths, git_commit, env_loader, driver_factory, observer=_default_observer, finalizer=finalize_s5_pstar_result):
    prior = _idempotent(paths)
    if prior is not None: return prior
    run, expected, terminals, accounting = _prerequisites(paths, git_commit)
    driver = None; stage = "environment_loading"; post = None
    try:
        env = env_loader(); stage = "driver_construction"; driver = await _await(driver_factory(env))
        stage = "observation"; post = verify_s5_pstar_post_observation(await _await(observer(driver=driver, run=run, expected_sources=expected, source_terminals=terminals)))
        stage = "observation_persist"; _exclusive(paths.post_observation, post)
    except Exception as error:
        failure = error
    else: failure = None
    finally:
        if driver is not None:
            try: await _await(driver.close())
            except Exception as error:
                if failure is None: stage, failure = "driver_close", error
    result_artifact = None
    if failure is None:
        stage = "finalization"
        try:
            result_artifact = verify_s5_pstar_result(await _await(finalizer(paths=paths, git_commit=git_commit)))
        except Exception as error: failure = error
    if failure is not None:
        checkpoint = _seal_checkpoint(status="incomplete_non_mergeable", failure_stage=stage, error_class=f"{type(failure).__module__}.{type(failure).__qualname__}", post=post, result_payload_sha256=None, accounting=accounting)
        _exclusive(_checkpoint_path(paths), checkpoint)
        return {"status": "incomplete_non_mergeable", "failure_stage": stage, "error_class": checkpoint["error_class"], **_FLAGS}
    checkpoint = _seal_checkpoint(status="complete", failure_stage=None, error_class=None, post=post, result_payload_sha256=result_artifact["payload_sha256"], accounting=accounting)
    _exclusive(_checkpoint_path(paths), checkpoint); inspect_s5_pstar_postprocess_checkpoint(_checkpoint_path(paths))
    return {"status": "SCIENTIFIC_OUTCOME_COMPLETE", "method": "P*", "post_observation_status": post["status"], "terminal_accounting": accounting, **_FLAGS}


def _production_env(path): return _default_env_file_loader(Path(path), _LEGACY_SRC)
def _production_driver(env):
    from neo4j import AsyncGraphDatabase
    if not isinstance(env, Mapping) or env.get("NEO4J_URI") != "bolt://localhost:7687": raise _fail("neo4j_environment_invalid")
    return AsyncGraphDatabase.driver(env["NEO4J_URI"], auth=(env.get("NEO4J_USER"), env.get("NEO4J_PASSWORD")))


def build_parser():
    parser = argparse.ArgumentParser(description="Observe and finalize S5 P*(C=2)")
    for name in ("production-identity", "production-identity-qualification", "preflight", "authority", "predecessor", "run-root"): parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--git-commit", required=True); parser.add_argument("--current-stage-pointer", type=Path, default=_PROJECT / "runtime/CURRENT_STAGE_STATUS.json"); parser.add_argument("--env-file", type=Path, default=_LEGACY / ".env")
    return parser


def main(argv: Sequence[str] | None = None):
    args = build_parser().parse_args(argv); root = args.run_root
    paths = S5PStarFinalizerPaths(production_identity=args.production_identity, production_identity_qualification=args.production_identity_qualification, current_stage_pointer=args.current_stage_pointer, preflight=args.preflight, authority=args.authority, predecessor=args.predecessor, consumption=root / "authority_consumption.json", controller_root=root / "controller", attempt_root=root / "attempt", post_observation=root / "post_observation.json", result=root / "S5_PSTAR_RESULT.json")
    try: result = asyncio.run(execute_s5_pstar_postprocess(paths=paths, git_commit=args.git_commit, env_loader=lambda: _production_env(args.env_file), driver_factory=_production_driver))
    except Exception as error:
        print(json.dumps({"status": "error", "error_class": type(error).__name__}, sort_keys=True), file=sys.stderr); return 1
    print(json.dumps(result, sort_keys=True)); return 0 if result.get("status") == "SCIENTIFIC_OUTCOME_COMPLETE" else 2


__all__ = ["CHECKPOINT_SCHEMA", "S5PStarPostprocessError", "build_parser", "execute_s5_pstar_postprocess", "inspect_s5_pstar_postprocess_checkpoint", "main"]
if __name__ == "__main__": raise SystemExit(main())
