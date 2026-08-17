"""Thin S6 identity adapter over the qualified grid-capable M* core."""

from __future__ import annotations

import inspect
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .s5_mstar_pipeline import (
    SCHEMA as CORE_SCHEMA,
    MSTAR,
    MStarPipelineError,
    MStarSource,
    MStarSpec,
    run_mstar_pipeline,
    verify_mstar_pipeline_evidence,
)
from .s6_calibration_contract import CONCURRENCIES, DEVELOPMENT_HISTORIES


SCHEMA = "membind.paper-eval-v3.s6-mstar-grid-evidence.v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(
    rf"^s6-({'|'.join(DEVELOPMENT_HISTORIES)})-mstar-c(1|2|4|8)-001$"
)

SemanticPrepare = Callable[[object, int], Awaitable[object]]
LatestStateBind = Callable[[object, int, int, tuple[int, ...]], Awaitable[object]]
PersistEvent = Callable[[Mapping[str, object]], Awaitable[object]]
ClockNs = Callable[[], int]
RecoverPublication = Callable[[MStarSource, int], Awaitable[object]]


class S6MStarGridError(ValueError):
    """The S6 cell identity or qualified M* core composition failed closed."""


def _fail(code: str) -> S6MStarGridError:
    return S6MStarGridError(code)


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _fail(code)
    return value


@dataclass(frozen=True)
class S6MStarSpec:
    run_id: str
    configured_concurrency: int
    production_core_identity_sha256: str
    execution_identity_sha256: str

    def __post_init__(self) -> None:
        if self.configured_concurrency not in CONCURRENCIES:
            raise _fail("configured_concurrency_invalid")
        match = _RUN_ID.fullmatch(self.run_id) if isinstance(self.run_id, str) else None
        if match is None or int(match.group(2)) != self.configured_concurrency:
            raise _fail("run_id_invalid")
        _sha(
            self.production_core_identity_sha256,
            "production_core_identity_invalid",
        )
        _sha(self.execution_identity_sha256, "execution_identity_invalid")

    @property
    def require_prepare_overlap(self) -> bool:
        return self.configured_concurrency > 1


def _inner_run_id(spec: S6MStarSpec) -> str:
    match = _RUN_ID.fullmatch(spec.run_id)
    if match is None:  # pragma: no cover - dataclass validation owns this path.
        raise _fail("run_id_invalid")
    return (
        f"s5-mstar-s6-{match.group(1)}-c{spec.configured_concurrency}-001"
    )


def _inner_spec(spec: S6MStarSpec) -> MStarSpec:
    return MStarSpec(
        run_id=_inner_run_id(spec),
        production_core_identity_sha256=spec.production_core_identity_sha256,
        prepare_concurrency=spec.configured_concurrency,
        require_prepare_overlap=spec.require_prepare_overlap,
    )


def _event_run_id(event: Mapping[str, object], run_id: str) -> dict[str, object]:
    selected = deepcopy(dict(event))
    selected["run_id"] = run_id
    return selected


def _outer_evidence(
    inner: Mapping[str, object], spec: S6MStarSpec
) -> dict[str, object]:
    selected = deepcopy(dict(inner))
    selected["schema_version"] = SCHEMA
    selected["run_id"] = spec.run_id
    selected["configured_concurrency"] = spec.configured_concurrency
    selected["require_prepare_overlap"] = spec.require_prepare_overlap
    selected["execution_identity_sha256"] = spec.execution_identity_sha256
    selected["qualified_core_schema_version"] = CORE_SCHEMA
    selected["events"] = [
        _event_run_id(event, spec.run_id) for event in selected["events"]
    ]
    return selected


def _inner_evidence(
    outer: Mapping[str, object], spec: S6MStarSpec
) -> dict[str, object]:
    selected = deepcopy(dict(outer))
    for field in (
        "configured_concurrency",
        "require_prepare_overlap",
        "execution_identity_sha256",
        "qualified_core_schema_version",
    ):
        selected.pop(field, None)
    selected["schema_version"] = CORE_SCHEMA
    selected["run_id"] = _inner_run_id(spec)
    events = selected.get("events")
    if isinstance(events, (str, bytes)) or not isinstance(events, Sequence):
        raise _fail("events_invalid")
    selected["events"] = [
        _event_run_id(event, _inner_run_id(spec))
        if isinstance(event, Mapping)
        else {}
        for event in events
    ]
    return selected


async def run_s6_mstar(
    *,
    spec: S6MStarSpec,
    sources: Sequence[MStarSource],
    semantic_prepare: SemanticPrepare,
    latest_state_bind: LatestStateBind,
    persist_event: PersistEvent,
    clock_ns: ClockNs,
    recover_publication: RecoverPublication | None = None,
) -> dict[str, object]:
    """Execute the existing core while exposing only the exact S6 cell ID."""

    if not isinstance(spec, S6MStarSpec):
        raise _fail("spec_invalid")
    if not callable(semantic_prepare) or not callable(latest_state_bind):
        raise _fail("callback_not_callable")
    if not callable(persist_event):
        raise _fail("persist_event_not_callable")
    callback_failures: list[BaseException] = []

    async def prepare(source: object, logical_time_ns: int) -> object:
        try:
            outcome = semantic_prepare(source, logical_time_ns)
            if not inspect.isawaitable(outcome):
                raise TypeError("semantic_prepare must be async")
            return await outcome
        except Exception as error:
            callback_failures.append(error)
            raise

    async def bind(
        prepared: object,
        logical_time_ns: int,
        source_sequence: int,
        visible_prefix: tuple[int, ...],
    ) -> object:
        try:
            outcome = latest_state_bind(
                prepared,
                logical_time_ns,
                source_sequence,
                visible_prefix,
            )
            if not inspect.isawaitable(outcome):
                raise TypeError("latest_state_bind must be async")
            return await outcome
        except Exception as error:
            callback_failures.append(error)
            raise

    async def persist(event: Mapping[str, object]) -> object:
        outcome = persist_event(_event_run_id(event, spec.run_id))
        if not inspect.isawaitable(outcome):
            raise TypeError("persist_event must be async")
        return await outcome

    recovery: RecoverPublication | None = None
    if recover_publication is not None:

        async def recover(source: MStarSource, logical_time_ns: int) -> object:
            try:
                outcome = recover_publication(source, logical_time_ns)
                if not inspect.isawaitable(outcome):
                    raise TypeError("recover_publication must be async")
                return await outcome
            except Exception as error:
                callback_failures.append(error)
                raise

        recovery = recover

    try:
        inner = await run_mstar_pipeline(
            spec=_inner_spec(spec),
            sources=sources,
            semantic_prepare=prepare,
            latest_state_bind=bind,
            persist_event=persist,
            clock_ns=clock_ns,
            recover_publication=recovery,
        )
    except MStarPipelineError:
        raise _fail("mstar_core_qualification_failed") from None
    if callback_failures:
        raise callback_failures[0]
    if inner.get("status") != "PASS":
        raise _fail("mstar_core_qualification_failed")
    outer = _outer_evidence(inner, spec)
    return verify_s6_mstar_evidence(
        outer, expected_spec=spec, expected_sources=sources
    )


def verify_s6_mstar_evidence(
    value: Mapping[str, object],
    *,
    expected_spec: S6MStarSpec,
    expected_sources: Sequence[MStarSource],
) -> dict[str, object]:
    if not isinstance(value, Mapping) or not isinstance(expected_spec, S6MStarSpec):
        raise _fail("evidence_or_spec_invalid")
    evidence = deepcopy(dict(value))
    expected_fields = {
        "schema_version",
        "run_id",
        "method",
        "configured_concurrency",
        "require_prepare_overlap",
        "production_core_identity_sha256",
        "execution_identity_sha256",
        "qualified_core_schema_version",
        "status",
        "mergeable",
        "failure_code",
        "semantic_error_code",
        "upstream_error_class",
        "events",
        "summary",
    }
    if set(evidence) != expected_fields:
        raise _fail("evidence_shape_invalid")
    if (
        evidence.get("schema_version") != SCHEMA
        or evidence.get("run_id") != expected_spec.run_id
        or evidence.get("method") != MSTAR
        or evidence.get("configured_concurrency")
        != expected_spec.configured_concurrency
        or evidence.get("require_prepare_overlap")
        is not expected_spec.require_prepare_overlap
        or evidence.get("production_core_identity_sha256")
        != expected_spec.production_core_identity_sha256
        or evidence.get("execution_identity_sha256")
        != expected_spec.execution_identity_sha256
        or evidence.get("qualified_core_schema_version") != CORE_SCHEMA
        or evidence.get("status") != "PASS"
        or evidence.get("mergeable") is not True
    ):
        raise _fail("evidence_identity_or_status_invalid")
    events = evidence.get("events")
    if (
        isinstance(events, (str, bytes))
        or not isinstance(events, Sequence)
        or any(
            not isinstance(event, Mapping)
            or event.get("run_id") != expected_spec.run_id
            for event in events
        )
    ):
        raise _fail("outer_event_identity_invalid")
    try:
        verify_mstar_pipeline_evidence(
            _inner_evidence(evidence, expected_spec),
            expected_spec=_inner_spec(expected_spec),
            expected_sources=expected_sources,
        )
    except (MStarPipelineError, ValueError):
        raise _fail("qualified_core_evidence_invalid") from None
    return evidence


__all__ = [
    "SCHEMA",
    "S6MStarGridError",
    "S6MStarSpec",
    "run_s6_mstar",
    "verify_s6_mstar_evidence",
]
