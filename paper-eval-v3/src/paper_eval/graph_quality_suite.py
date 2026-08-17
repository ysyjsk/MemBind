"""Resumable artifacts and aggregation for the three-method QA overlay.

Each evaluated question is first sealed into a private bundle containing both
the private audit material and its public projection.  If a process dies before
the public file is written, the projection can be restored without another
model request.  This prevents disconnect recovery from becoming answer
resampling.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .artifacts import atomic_write_json, payload_sha256
from .baseline_suite import (
    DEVELOPMENT_HISTORIES,
    baseline_block_namespace,
    build_baseline_suite_plan,
)
from .baseline_suite_artifacts import inspect_baseline_block
from .baseline_suite_u0_reuse import verify_reusable_u0_run
from .graph_quality_overlay import GraphQualityQuestionResult
from .native_baseline_runner import build_native_baseline_plan


METHODS = ("U0", "A0", "P(C=2)")
METHOD_SLUGS = {"U0": "u0", "A0": "a0", "P(C=2)": "pc2"}
PRIVATE_BUNDLE_SCHEMA = "membind.paper-eval-v3.graph-quality-bundle.v1"
CLAIM_LABEL = "PROTOCOL_RUBRIC_COMPATIBLE_LOCAL_JUDGE_DIAGNOSTIC"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OVERLAY_RUN_ID = re.compile(r"^gq-[a-z0-9][a-z0-9-]{2,63}$")


class GraphQualitySuiteError(ValueError):
    """The graph-quality target, artifact, or aggregate failed closed."""


@dataclass(frozen=True)
class GraphQualityDiscoveryHooks:
    """Read-only verification boundaries used by target discovery tests."""

    verify_u0: Callable[[Path, str], dict[str, Any]]
    inspect_block: Callable[[Path, Mapping[str, object]], dict[str, object]]


DEFAULT_DISCOVERY_HOOKS = GraphQualityDiscoveryHooks(
    verify_u0=verify_reusable_u0_run,
    inspect_block=inspect_baseline_block,
)


def _sha(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise GraphQualitySuiteError(f"{field} is invalid")
    return value


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise GraphQualitySuiteError(f"{field} is invalid")
    return value


def _read_object(path: Path, *, label: str) -> dict[str, Any]:
    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise GraphQualitySuiteError(f"{label} contains a duplicate field")
            result[key] = value
        return result

    try:
        value = json.loads(
            Path(path).read_text(encoding="utf-8"),
            object_pairs_hook=no_duplicates,
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise GraphQualitySuiteError(f"{label} is unreadable") from None
    if not isinstance(value, dict):
        raise GraphQualitySuiteError(f"{label} is invalid")
    return value


def _verify_final_suite_report(
    path: Path,
    *,
    suite_run_id: str,
    native_run_id: str,
    u0_payload_sha256: str,
) -> dict[str, Any]:
    report = _read_object(path, label="final suite report")
    observed_hash = report.get("payload_sha256")
    if observed_hash != payload_sha256(
        {key: value for key, value in report.items() if key != "payload_sha256"}
    ):
        raise GraphQualitySuiteError("final suite report hash mismatch")
    fairness = report.get("fairness")
    u0 = report.get("u0")
    blocks = report.get("blocks")
    if (
        report.get("schema_version")
        != "membind.paper-eval-v3.three-baseline-report.v1"
        or report.get("run_id") != suite_run_id
        or report.get("status") != "PASS"
        or report.get("execution_order") != ["U0_REUSED", "A0", "P(C=2)"]
        or not isinstance(fairness, Mapping)
        or fairness.get("quality_identity_verified") is not True
        or not isinstance(u0, Mapping)
        or u0.get("source_run_id") != native_run_id
        or u0.get("payload_sha256") != u0_payload_sha256
        or not isinstance(blocks, list)
    ):
        raise GraphQualitySuiteError("final suite report identity is invalid")
    expected = [
        (method, history_id)
        for method in ("A0", "P(C=2)")
        for history_id in DEVELOPMENT_HISTORIES
    ]
    observed = [
        (value.get("method"), value.get("history_id"))
        for value in blocks
        if isinstance(value, Mapping)
    ]
    if observed != expected or len(blocks) != len(expected):
        raise GraphQualitySuiteError("final suite report block inventory is invalid")
    return report


@dataclass(frozen=True)
class GraphQualityTarget:
    """One sealed construction graph eligible for read-only evaluation."""

    method: str
    history_id: str
    namespace: str
    episode_count: int
    construction_result_sha256: str

    def __post_init__(self) -> None:
        if self.method not in METHODS:
            raise GraphQualitySuiteError("target method is invalid")
        if self.history_id not in DEVELOPMENT_HISTORIES:
            raise GraphQualitySuiteError("target history is invalid")
        _text(self.namespace, field="target namespace")
        if (
            isinstance(self.episode_count, bool)
            or not isinstance(self.episode_count, int)
            or self.episode_count < 1
        ):
            raise GraphQualitySuiteError("target episode count is invalid")
        _sha(
            self.construction_result_sha256,
            field="target construction result SHA256",
        )


def verify_target_inventory(
    targets: Sequence[GraphQualityTarget],
) -> tuple[GraphQualityTarget, ...]:
    """Require exactly four development histories for each of three methods."""

    if isinstance(targets, (str, bytes)):
        raise GraphQualitySuiteError("target inventory is invalid")
    observed = tuple(targets)
    if any(not isinstance(value, GraphQualityTarget) for value in observed):
        raise GraphQualitySuiteError("target inventory is invalid")
    expected = tuple(
        (method, history_id)
        for method in METHODS
        for history_id in DEVELOPMENT_HISTORIES
    )
    identities = tuple((value.method, value.history_id) for value in observed)
    if identities != expected or len(set(identities)) != len(expected):
        raise GraphQualitySuiteError("target inventory is incomplete or reordered")
    if len({value.namespace for value in observed}) != len(observed):
        raise GraphQualitySuiteError("target inventory has duplicate namespaces")
    if len({value.construction_result_sha256 for value in observed}) != len(observed):
        raise GraphQualitySuiteError("target inventory has duplicate result identities")
    return observed


def _attempt_block(base: Mapping[str, Any], attempt: int) -> dict[str, Any]:
    block = deepcopy(dict(base))
    block["attempt_ordinal"] = attempt
    block["namespace"] = baseline_block_namespace(
        suite_run_id=str(block["suite_run_id"]),
        method=str(block["method"]),
        history_id=str(block["history_id"]),
        attempt_ordinal=attempt,
    )
    return block


def _completed_live_target(
    *,
    suite_run_root: Path,
    base: Mapping[str, Any],
    expected_episode_count: int,
    inspect_block: Callable[[Path, Mapping[str, object]], dict[str, object]],
) -> GraphQualityTarget:
    method = str(base["method"])
    history_id = str(base["history_id"])
    history_root = (
        Path(suite_run_root)
        / "blocks"
        / METHOD_SLUGS[method]
        / history_id
    )
    completed: list[GraphQualityTarget] = []
    if history_root.is_dir() and not history_root.is_symlink():
        for root in sorted(history_root.iterdir()):
            match = re.fullmatch(r"attempt-([0-9]{3})", root.name)
            if match is None or not root.is_dir() or root.is_symlink():
                raise GraphQualitySuiteError("live attempt inventory is invalid")
            attempt = int(match.group(1))
            if not 1 <= attempt <= 999:
                raise GraphQualitySuiteError("live attempt inventory is invalid")
            block = _attempt_block(base, attempt)
            observed = inspect_block(root, block)
            if not (
                observed.get("status") == "completed"
                and observed.get("artifacts_verified") is True
            ):
                continue
            result = observed.get("result")
            if not isinstance(result, Mapping):
                raise GraphQualitySuiteError("completed live block result is missing")
            payload = result.get("payload")
            if (
                not isinstance(payload, Mapping)
                or payload.get("run_id") != block["namespace"]
                or payload.get("method") != method
                or payload.get("history_id") != history_id
                or payload.get("status") != "PASS"
                or payload.get("episode_count") != expected_episode_count
            ):
                raise GraphQualitySuiteError("completed live block identity drift")
            completed.append(
                GraphQualityTarget(
                    method=method,
                    history_id=history_id,
                    namespace=str(block["namespace"]),
                    episode_count=expected_episode_count,
                    construction_result_sha256=_sha(
                        result.get("result_payload_sha256"),
                        field="live result payload SHA256",
                    ),
                )
            )
    if len(completed) != 1:
        raise GraphQualitySuiteError(
            "construction suite is not complete with exactly one verified attempt"
        )
    return completed[0]


def discover_graph_quality_targets(
    *,
    native_runs_root: Path,
    suite_runs_root: Path,
    native_run_id: str,
    suite_run_id: str,
    hooks: GraphQualityDiscoveryHooks = DEFAULT_DISCOVERY_HOOKS,
) -> tuple[GraphQualityTarget, ...]:
    """Discover only verified U0/A0/P graphs; partial attempts are never eligible."""

    try:
        u0 = hooks.verify_u0(Path(native_runs_root), native_run_id)
        native_plan = build_native_baseline_plan(native_run_id)
        suite_plan = build_baseline_suite_plan(
            suite_run_id,
            mode="development",
            reuse_u0_run=native_run_id,
        )
    except GraphQualitySuiteError:
        raise
    except Exception as error:
        raise GraphQualitySuiteError(
            f"construction target verification failed: {type(error).__name__}"
        ) from None
    histories = u0.get("histories")
    if not isinstance(histories, list):
        raise GraphQualitySuiteError("U0 target inventory is invalid")
    u0_by_history = {
        str(value.get("history_id")): value
        for value in histories
        if isinstance(value, Mapping)
    }
    if tuple(u0_by_history) != tuple(DEVELOPMENT_HISTORIES):
        raise GraphQualitySuiteError("U0 target inventory is invalid")
    suite_root = Path(suite_runs_root) / suite_run_id
    suite_report = _verify_final_suite_report(
        suite_root / "THREE_BASELINE_RESULTS.json",
        suite_run_id=suite_run_id,
        native_run_id=native_run_id,
        u0_payload_sha256=_sha(
            u0.get("payload_sha256"), field="U0 reuse payload SHA256"
        ),
    )
    native_by_history = {
        value.history_id: value for value in native_plan.histories
    }
    targets: list[GraphQualityTarget] = []
    episode_counts: dict[str, int] = {}
    for history_id in DEVELOPMENT_HISTORIES:
        source = u0_by_history[history_id]
        count = source.get("episode_count")
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise GraphQualitySuiteError("U0 target episode count is invalid")
        episode_counts[history_id] = count
        targets.append(
            GraphQualityTarget(
                method="U0",
                history_id=history_id,
                namespace=native_by_history[history_id].namespace,
                episode_count=count,
                construction_result_sha256=_sha(
                    source.get("history_result_payload_sha256"),
                    field="U0 history result SHA256",
                ),
            )
        )
    bases = {
        (str(block["method"]), str(block["history_id"])): block
        for block in suite_plan["blocks"]
        if block["method"] in {"A0", "P(C=2)"}
    }
    for method in ("A0", "P(C=2)"):
        for history_id in DEVELOPMENT_HISTORIES:
            targets.append(
                _completed_live_target(
                    suite_run_root=suite_root,
                    base=bases[(method, history_id)],
                    expected_episode_count=episode_counts[history_id],
                    inspect_block=hooks.inspect_block,
                )
            )
    verified = verify_target_inventory(targets)
    report_blocks = suite_report["blocks"]
    live_targets = verified[len(DEVELOPMENT_HISTORIES) :]
    for report_row, target in zip(report_blocks, live_targets, strict=True):
        if (
            not isinstance(report_row, Mapping)
            or report_row.get("method") != target.method
            or report_row.get("history_id") != target.history_id
            or report_row.get("episode_count") != target.episode_count
            or report_row.get("result_payload_sha256")
            != target.construction_result_sha256
        ):
            raise GraphQualitySuiteError(
                "final suite report and completed target binding drift"
            )
    return verified


def _verify_public(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise GraphQualitySuiteError("public artifact is invalid")
    public = dict(value)
    observed_hash = public.get("payload_sha256")
    expected_hash = payload_sha256(
        {key: child for key, child in public.items() if key != "payload_sha256"}
    )
    if observed_hash != expected_hash:
        raise GraphQualitySuiteError("public artifact hash mismatch")
    if public.get("schema_version") != (
        "membind.paper-eval-v3.graph-quality-public.v1"
    ):
        raise GraphQualitySuiteError("public artifact schema mismatch")
    _text(public.get("overlay_run_id"), field="overlay run id")
    _sha(public.get("namespace_sha256"), field="namespace SHA256")
    _sha(
        public.get("construction_result_sha256"),
        field="construction result SHA256",
    )
    runtime_identity = public.get("runtime_identity")
    if not isinstance(runtime_identity, Mapping) or not runtime_identity:
        raise GraphQualitySuiteError("runtime identity is invalid")
    if public.get("runtime_identity_sha256") != payload_sha256(runtime_identity):
        raise GraphQualitySuiteError("runtime identity hash mismatch")
    _sha(public.get("private_artifact_sha256"), field="private artifact SHA256")
    identity = public.get("quality_identity")
    if not isinstance(identity, Mapping) or set(identity) != {
        "retrieval_config_sha256",
        "reader_config_sha256",
        "judge_config_sha256",
    }:
        raise GraphQualitySuiteError("quality identity is invalid")
    for field, child in identity.items():
        _sha(child, field=field)
    denominator = public.get("judge_valid_denominator")
    qa = public.get("qa_accuracy")
    if denominator not in {0, 1} or isinstance(denominator, bool):
        raise GraphQualitySuiteError("Judge denominator is invalid")
    if denominator == 0:
        if qa is not None or public.get("headline_eligible") is not False:
            raise GraphQualitySuiteError("invalid Judge entered the QA denominator")
    elif (
        isinstance(qa, bool)
        or not isinstance(qa, (int, float))
        or float(qa) not in {0.0, 1.0}
        or public.get("headline_eligible") is not True
    ):
        raise GraphQualitySuiteError("valid Judge result is inconsistent")
    coverage = public.get("edge_attributed_source_coverage_at_10")
    if (
        isinstance(coverage, bool)
        or not isinstance(coverage, (int, float))
        or not 0.0 <= float(coverage) <= 1.0
    ):
        raise GraphQualitySuiteError("edge-attributed coverage is invalid")
    return public


def _quality_identity(value: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {
        "retrieval_config_sha256",
        "reader_config_sha256",
        "judge_config_sha256",
    }:
        raise GraphQualitySuiteError("quality identity is invalid")
    return {
        field: _sha(child, field=field)
        for field, child in value.items()
    }


def _runtime_identity(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or not value:
        raise GraphQualitySuiteError("runtime identity is invalid")
    return deepcopy(dict(value))


def _bundle(
    *,
    public_artifact: Mapping[str, Any],
    private_artifact: Mapping[str, Any],
) -> dict[str, Any]:
    public = _verify_public(public_artifact)
    if not isinstance(private_artifact, Mapping):
        raise GraphQualitySuiteError("private artifact is invalid")
    private = dict(private_artifact)
    if public["private_artifact_sha256"] != payload_sha256(private):
        raise GraphQualitySuiteError("public/private artifact binding mismatch")
    body: dict[str, Any] = {
        "schema_version": PRIVATE_BUNDLE_SCHEMA,
        "public_artifact": public,
        "private_artifact": private,
    }
    body["bundle_sha256"] = payload_sha256(body)
    return body


def _verify_bundle(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise GraphQualitySuiteError("private bundle is invalid")
    bundle = dict(value)
    observed_hash = bundle.get("bundle_sha256")
    if observed_hash != payload_sha256(
        {key: child for key, child in bundle.items() if key != "bundle_sha256"}
    ):
        raise GraphQualitySuiteError("private bundle hash mismatch")
    if bundle.get("schema_version") != PRIVATE_BUNDLE_SCHEMA:
        raise GraphQualitySuiteError("private bundle schema mismatch")
    expected = _bundle(
        public_artifact=bundle.get("public_artifact", {}),
        private_artifact=bundle.get("private_artifact", {}),
    )
    if bundle != expected:
        raise GraphQualitySuiteError("private bundle content mismatch")
    return bundle


def persist_question_bundle(
    attempt_root: Path,
    *,
    public_artifact: Mapping[str, Any],
    private_artifact: Mapping[str, Any],
) -> dict[str, Any]:
    """Seal private material before publishing its content-free projection."""

    root = Path(attempt_root)
    candidate = _bundle(
        public_artifact=public_artifact,
        private_artifact=private_artifact,
    )
    bundle_path = root / "private_bundle.json"
    public_path = root / "public.json"
    if bundle_path.exists():
        existing = _verify_bundle(_read_object(bundle_path, label="private bundle"))
        if existing != candidate:
            raise GraphQualitySuiteError("existing private bundle drift")
    elif public_path.exists():
        raise GraphQualitySuiteError("existing public artifact has no private bundle")
    else:
        atomic_write_json(bundle_path, candidate)
    public = dict(candidate["public_artifact"])
    if public_path.exists():
        if _read_object(public_path, label="public artifact") != public:
            raise GraphQualitySuiteError("existing public artifact drift")
    else:
        atomic_write_json(public_path, public)
    return _verify_public(public)


def load_or_restore_question_bundle(attempt_root: Path) -> dict[str, Any]:
    """Restore a missing public file from a sealed private bundle, without LLM I/O."""

    root = Path(attempt_root)
    bundle = _verify_bundle(
        _read_object(root / "private_bundle.json", label="private bundle")
    )
    public = dict(bundle["public_artifact"])
    public_path = root / "public.json"
    if public_path.exists():
        if _read_object(public_path, label="public artifact") != public:
            raise GraphQualitySuiteError("existing public artifact drift")
    else:
        atomic_write_json(public_path, public)
    return _verify_public(public)


def _target_payload(target: GraphQualityTarget) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema_version": "membind.paper-eval-v3.graph-quality-target.v1",
        "method": target.method,
        "history_id": target.history_id,
        "namespace": target.namespace,
        "episode_count": target.episode_count,
        "construction_result_sha256": target.construction_result_sha256,
    }
    body["payload_sha256"] = payload_sha256(body)
    return body


def graph_quality_targets_sha256(
    targets: Sequence[GraphQualityTarget],
) -> str:
    """Hash the exact verified 12-target inventory in canonical order."""

    inventory = verify_target_inventory(targets)
    return payload_sha256([_target_payload(value) for value in inventory])


def verify_public_target(
    public: Mapping[str, Any],
    target: GraphQualityTarget,
    *,
    overlay_run_id: str,
    runtime_identity: Mapping[str, Any],
    quality_identity: Mapping[str, str],
) -> dict[str, Any]:
    value = _verify_public(public)
    if (
        value.get("overlay_run_id") != overlay_run_id
        or value.get("method") != target.method
        or value.get("history_id") != target.history_id
        or value.get("namespace_sha256")
        != hashlib.sha256(target.namespace.encode("utf-8")).hexdigest()
        or value.get("construction_result_sha256")
        != target.construction_result_sha256
        or value.get("runtime_identity") != dict(runtime_identity)
        or value.get("runtime_identity_sha256")
        != payload_sha256(runtime_identity)
        or value.get("quality_identity") != dict(quality_identity)
    ):
        raise GraphQualitySuiteError("public artifact target identity drift")
    return value


def _attempt_inventory(unit_root: Path) -> list[tuple[int, Path]]:
    if not unit_root.exists():
        return []
    if not unit_root.is_dir() or unit_root.is_symlink():
        raise GraphQualitySuiteError("overlay unit root is invalid")
    attempts: list[tuple[int, Path]] = []
    for path in sorted(unit_root.iterdir()):
        match = re.fullmatch(r"attempt-([0-9]{3})", path.name)
        if match is None or not path.is_dir() or path.is_symlink():
            raise GraphQualitySuiteError("overlay attempt inventory is invalid")
        ordinal = int(match.group(1))
        if not 1 <= ordinal <= 999:
            raise GraphQualitySuiteError("overlay attempt inventory is invalid")
        attempts.append((ordinal, path))
    if [value for value, _path in attempts] != list(
        range(1, len(attempts) + 1)
    ):
        raise GraphQualitySuiteError("overlay attempt inventory is not contiguous")
    return attempts


def _select_overlay_attempt(
    run_root: Path,
    target: GraphQualityTarget,
    *,
    overlay_run_id: str,
    runtime_identity: Mapping[str, Any],
    quality_identity: Mapping[str, str],
) -> tuple[str, int, Path, dict[str, Any] | None]:
    unit_root = (
        Path(run_root)
        / "units"
        / METHOD_SLUGS[target.method]
        / target.history_id
    )
    attempts = _attempt_inventory(unit_root)
    completed: list[tuple[int, Path, dict[str, Any]]] = []
    for ordinal, path in attempts:
        if (path / "private_bundle.json").exists():
            public = verify_public_target(
                load_or_restore_question_bundle(path),
                target,
                overlay_run_id=overlay_run_id,
                runtime_identity=runtime_identity,
                quality_identity=quality_identity,
            )
            completed.append((ordinal, path, public))
        elif (path / "public.json").exists():
            raise GraphQualitySuiteError("public artifact has no recoverable bundle")
    if len(completed) > 1:
        raise GraphQualitySuiteError("overlay unit has multiple completed attempts")
    if completed:
        ordinal, path, public = completed[0]
        return "SKIP", ordinal, path, public
    next_attempt = len(attempts) + 1
    if next_attempt > 999:
        raise GraphQualitySuiteError("overlay unit exhausted attempt ordinals")
    return (
        "RUN",
        next_attempt,
        unit_root / f"attempt-{next_attempt:03d}",
        None,
    )


@dataclass(frozen=True)
class _OverlayExecutionUnit:
    """One immutable action selected only after its existing attempts verify."""

    target: GraphQualityTarget
    action: str
    attempt: int
    attempt_root: Path
    public: dict[str, Any] | None


def _freeze_overlay_execution_plan(
    *,
    run_root: Path,
    inventory: Sequence[GraphQualityTarget],
    overlay_run_id: str,
    runtime_identity: Mapping[str, Any],
    quality_identity: Mapping[str, str],
) -> tuple[_OverlayExecutionUnit, ...]:
    """Verify/recover every unit before any live evaluator can be called."""

    plan: list[_OverlayExecutionUnit] = []
    for target in inventory:
        action, attempt, attempt_root, public = _select_overlay_attempt(
            run_root,
            target,
            overlay_run_id=overlay_run_id,
            runtime_identity=runtime_identity,
            quality_identity=quality_identity,
        )
        plan.append(
            _OverlayExecutionUnit(
                target=target,
                action=action,
                attempt=attempt,
                attempt_root=attempt_root,
                public=deepcopy(public),
            )
        )
    return tuple(plan)


def _progress(
    *,
    overlay_run_id: str,
    status: str,
    completed: Sequence[Mapping[str, Any]],
    error_class: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema_version": "membind.paper-eval-v3.graph-quality-progress.v1",
        "overlay_run_id": overlay_run_id,
        "status": status,
        "completed_unit_count": len(completed),
        "expected_unit_count": len(METHODS) * len(DEVELOPMENT_HISTORIES),
        "completed": [dict(value) for value in completed],
        "error_class": error_class,
    }
    body["payload_sha256"] = payload_sha256(body)
    return body


async def _run_graph_quality_targets_unlocked(
    *,
    overlay_run_id: str,
    targets: Sequence[GraphQualityTarget],
    run_root: Path,
    evaluate: Callable[
        [GraphQualityTarget, Path], Awaitable[GraphQualityQuestionResult]
    ],
    runtime_identity: Mapping[str, Any],
    quality_identity: Mapping[str, str],
) -> dict[str, Any]:
    """Evaluate or verify all 12 units without ever resampling completed output."""

    if not isinstance(overlay_run_id, str) or _OVERLAY_RUN_ID.fullmatch(
        overlay_run_id
    ) is None:
        raise GraphQualitySuiteError("overlay run id is invalid")
    inventory = verify_target_inventory(targets)
    frozen_runtime_identity = _runtime_identity(runtime_identity)
    frozen_quality_identity = _quality_identity(quality_identity)
    root = Path(run_root)
    root.mkdir(parents=True, exist_ok=True)
    execution_plan = _freeze_overlay_execution_plan(
        run_root=root,
        inventory=inventory,
        overlay_run_id=overlay_run_id,
        runtime_identity=frozen_runtime_identity,
        quality_identity=frozen_quality_identity,
    )
    for unit in execution_plan:
        if unit.action == "RUN":
            atomic_write_json(
                unit.attempt_root / "target.json",
                _target_payload(unit.target),
            )
    completed: list[dict[str, Any]] = []
    public_rows: list[dict[str, Any]] = []
    for unit in execution_plan:
        target = unit.target
        action = unit.action
        attempt = unit.attempt
        attempt_root = unit.attempt_root
        public = deepcopy(unit.public)
        if action == "RUN":
            try:
                result = await evaluate(target, attempt_root)
                if not isinstance(result, GraphQualityQuestionResult):
                    raise GraphQualitySuiteError(
                        "graph quality evaluator returned an invalid result"
                    )
                public = persist_question_bundle(
                    attempt_root,
                    public_artifact=result.public_artifact,
                    private_artifact=result.private_artifact,
                )
                public = verify_public_target(
                    public,
                    target,
                    overlay_run_id=overlay_run_id,
                    runtime_identity=frozen_runtime_identity,
                    quality_identity=frozen_quality_identity,
                )
            except BaseException as error:
                error_class = (
                    f"{type(error).__module__}.{type(error).__qualname__}"
                )
                failure = {
                    "schema_version": (
                        "membind.paper-eval-v3.graph-quality-failure.v1"
                    ),
                    "status": "incomplete_non_mergeable",
                    "overlay_run_id": overlay_run_id,
                    "method": target.method,
                    "history_id": target.history_id,
                    "attempt_ordinal": attempt,
                    "error_class": error_class,
                }
                failure["payload_sha256"] = payload_sha256(failure)
                atomic_write_json(attempt_root / "failure.json", failure)
                atomic_write_json(
                    root / "progress.json",
                    _progress(
                        overlay_run_id=overlay_run_id,
                        status="STOPPED_ON_ERROR",
                        completed=completed,
                        error_class=error_class,
                    ),
                )
                raise
        assert public is not None
        row = {
            "method": target.method,
            "history_id": target.history_id,
            "attempt_ordinal": attempt,
            "disposition": "EXECUTED" if action == "RUN" else "SKIPPED_VERIFIED",
            "public_payload_sha256": public["payload_sha256"],
            "qa_accuracy": public["qa_accuracy"],
            "judge_valid_denominator": public["judge_valid_denominator"],
            "edge_attributed_source_coverage_at_10": public[
                "edge_attributed_source_coverage_at_10"
            ],
        }
        completed.append(row)
        public_rows.append(public)
        atomic_write_json(
            root / "progress.json",
            _progress(
                overlay_run_id=overlay_run_id,
                status="RUNNING",
                completed=completed,
            ),
        )
    summary = summarize_graph_quality_results(public_rows)
    sealed_units = [
        {
            key: value
            for key, value in row.items()
            if key != "disposition"
        }
        for row in completed
    ]
    report: dict[str, Any] = {
        "schema_version": "membind.paper-eval-v3.graph-quality-report.v1",
        "overlay_run_id": overlay_run_id,
        "status": "PASS",
        "target_count": len(inventory),
        "targets_sha256": graph_quality_targets_sha256(inventory),
        "summary": summary,
        "units": sealed_units,
    }
    report["payload_sha256"] = payload_sha256(report)
    report_path = root / "GRAPH_QUALITY_RESULTS.json"
    if report_path.exists():
        existing = _read_object(report_path, label="graph quality report")
        if existing != report:
            raise GraphQualitySuiteError("existing graph quality report drift")
    else:
        atomic_write_json(report_path, report)
    atomic_write_json(
        root / "progress.json",
        _progress(
            overlay_run_id=overlay_run_id,
            status="COMPLETED",
            completed=completed,
        ),
    )
    return report


async def run_graph_quality_targets(
    *,
    overlay_run_id: str,
    targets: Sequence[GraphQualityTarget],
    run_root: Path,
    evaluate: Callable[
        [GraphQualityTarget, Path], Awaitable[GraphQualityQuestionResult]
    ],
    runtime_identity: Mapping[str, Any],
    quality_identity: Mapping[str, str],
) -> dict[str, Any]:
    """Hold one exclusive run lock across verification and all live requests."""

    root = Path(run_root)
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / "run.lock"
    with lock_path.open("a+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise GraphQualitySuiteError(
                "graph-quality overlay run is already running"
            ) from None
        try:
            return await _run_graph_quality_targets_unlocked(
                overlay_run_id=overlay_run_id,
                targets=targets,
                run_root=root,
                evaluate=evaluate,
                runtime_identity=runtime_identity,
                quality_identity=quality_identity,
            )
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def summarize_graph_quality_results(
    public_artifacts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Aggregate the fixed 12-unit development overlay with an explicit denominator."""

    rows = [_verify_public(value) for value in public_artifacts]
    expected = [
        (method, history_id)
        for method in METHODS
        for history_id in DEVELOPMENT_HISTORIES
    ]
    observed = [(row.get("method"), row.get("history_id")) for row in rows]
    if observed != expected:
        raise GraphQualitySuiteError("result inventory is incomplete or reordered")
    identities = [dict(row["quality_identity"]) for row in rows]
    if any(value != identities[0] for value in identities[1:]):
        raise GraphQualitySuiteError("graph quality identity drift")
    runtime_identities = [dict(row["runtime_identity"]) for row in rows]
    if any(value != runtime_identities[0] for value in runtime_identities[1:]):
        raise GraphQualitySuiteError("graph quality runtime identity drift")
    valid = [row for row in rows if row["judge_valid_denominator"] == 1]
    invalid_count = len(rows) - len(valid)
    by_method: dict[str, dict[str, Any]] = {}
    for method in METHODS:
        selected = [row for row in rows if row["method"] == method]
        selected_valid = [
            row for row in selected if row["judge_valid_denominator"] == 1
        ]
        by_method[method] = {
            "question_count": len(selected),
            "valid_judge_count": len(selected_valid),
            "invalid_judge_count": len(selected) - len(selected_valid),
            "qa_accuracy": (
                sum(float(row["qa_accuracy"]) for row in selected_valid)
                / len(selected_valid)
                if selected_valid
                else None
            ),
            "edge_attributed_source_coverage_at_10_macro": sum(
                float(row["edge_attributed_source_coverage_at_10"])
                for row in selected
            )
            / len(selected),
        }
    return {
        "schema_version": "membind.paper-eval-v3.graph-quality-summary.v1",
        "question_count": len(rows),
        "valid_judge_count": len(valid),
        "invalid_judge_count": invalid_count,
        "qa_accuracy_micro": (
            sum(float(row["qa_accuracy"]) for row in valid) / len(valid)
            if valid
            else None
        ),
        "edge_attributed_source_coverage_at_10_macro": sum(
            float(row["edge_attributed_source_coverage_at_10"])
            for row in rows
        )
        / len(rows),
        "quality_identity": identities[0],
        "runtime_identity": runtime_identities[0],
        "runtime_identity_sha256": payload_sha256(runtime_identities[0]),
        "by_method": by_method,
        "claim_label": CLAIM_LABEL,
        "heldout_data_accessed": False,
        "construction_latency_includes_overlay": False,
    }


__all__ = [
    "CLAIM_LABEL",
    "GraphQualityDiscoveryHooks",
    "GraphQualitySuiteError",
    "GraphQualityTarget",
    "METHODS",
    "METHOD_SLUGS",
    "discover_graph_quality_targets",
    "graph_quality_targets_sha256",
    "load_or_restore_question_bundle",
    "persist_question_bundle",
    "run_graph_quality_targets",
    "summarize_graph_quality_results",
    "verify_public_target",
    "verify_target_inventory",
]
