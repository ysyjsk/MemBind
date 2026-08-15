"""Oracle-free M* production adapter for the offline FX0 parity lane.

The adapter reuses :mod:`s5_mstar_pipeline` for prepare concurrency, source
ordered bind, logical timestamps, and fail-closed publication evidence.  The
semantic callbacks are the only production boundaries supplied by the caller;
this module never receives fixture expectations and never constructs a second
MemBind algorithm.
"""

from __future__ import annotations

import inspect
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from copy import deepcopy
from typing import Any

from .artifacts import payload_sha256
from .fx0_mechanism_fixture import (
    ControlledNondeterminism,
    Fx0ExecutionCase,
    MechanismOutcome,
)
from .s5_mstar_pipeline import MStarSource, MStarSpec, run_mstar_pipeline


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
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
    "secret",
}


class S5MStarProductionAdapterError(ValueError):
    """Sanitized adapter or fail-closed fixture transition error."""

    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


def _assert_public(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or key.casefold() in _PRIVATE_FIELDS:
                raise S5MStarProductionAdapterError("PRIVATE_OUTPUT_FIELD")
            _assert_public(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _assert_public(child)


def _identity(value: Mapping[str, object]) -> dict[str, object]:
    identity = deepcopy(dict(value))
    if set(identity) != {"status", "method", "identity_sha256"}:
        raise S5MStarProductionAdapterError("PRODUCTION_IDENTITY_SHAPE_INVALID")
    if identity.get("status") != "FROZEN" or identity.get("method") != "M_STAR":
        raise S5MStarProductionAdapterError("PRODUCTION_IDENTITY_NOT_FROZEN")
    if not isinstance(identity.get("identity_sha256"), str) or _SHA256.fullmatch(
        identity["identity_sha256"]
    ) is None:
        raise S5MStarProductionAdapterError("PRODUCTION_IDENTITY_HASH_INVALID")
    return identity


SemanticPrepare = Callable[
    [Mapping[str, Any], int, ControlledNondeterminism], Awaitable[object]
]
LatestStateBind = Callable[
    [object, int, int, tuple[int, ...], ControlledNondeterminism],
    Awaitable[object],
]
Snapshot = Callable[[], tuple[Mapping[str, Any], Sequence[Mapping[str, Any]]]]
PersistEvent = Callable[[Mapping[str, object]], Awaitable[object]]
ClockNs = Callable[[], int]


class S5MStarProductionAdapter:
    """Bind real semantic callbacks to the shared M* scheduling core.

    ``execute_fixture_case`` receives only ``Fx0ExecutionCase`` and controlled
    providers.  Expected status, state, and history are intentionally absent
    from this interface; the FX0 comparator owns those values.
    """

    def __init__(
        self,
        *,
        production_path_identity: Mapping[str, object],
        production_core_identity_sha256: str,
        semantic_prepare: SemanticPrepare,
        latest_state_bind: LatestStateBind,
        snapshot: Snapshot,
        persist_event: PersistEvent | None = None,
        clock_ns: ClockNs = time.monotonic_ns,
    ) -> None:
        self.production_path_identity = _identity(production_path_identity)
        if production_core_identity_sha256 != self.production_path_identity[
            "identity_sha256"
        ]:
            raise S5MStarProductionAdapterError("PRODUCTION_CORE_IDENTITY_MISMATCH")
        if not callable(semantic_prepare) or not callable(latest_state_bind):
            raise S5MStarProductionAdapterError("SEMANTIC_CALLBACK_NOT_CALLABLE")
        if not callable(snapshot) or not callable(clock_ns):
            raise S5MStarProductionAdapterError("SNAPSHOT_OR_CLOCK_NOT_CALLABLE")
        self.production_core_identity_sha256 = production_core_identity_sha256
        self.semantic_prepare = semantic_prepare
        self.latest_state_bind = latest_state_bind
        self.snapshot = snapshot
        self.persist_event = persist_event or self._discard_event
        self.clock_ns = clock_ns

    @staticmethod
    async def _discard_event(_event: Mapping[str, object]) -> None:
        return None

    async def execute_fixture_case(
        self,
        case: Fx0ExecutionCase,
        providers: ControlledNondeterminism,
    ) -> MechanismOutcome:
        """Execute one oracle-free FX0 case through the shared M* core."""

        if not isinstance(case, Fx0ExecutionCase):
            raise S5MStarProductionAdapterError("FX0_EXECUTION_INPUT_INVALID")
        if not isinstance(providers, ControlledNondeterminism):
            raise S5MStarProductionAdapterError("CONTROLLED_PROVIDERS_INVALID")
        if not isinstance(case.source, Mapping):
            raise S5MStarProductionAdapterError("FX0_SOURCE_INVALID")
        if case.source_sequence != 0:
            raise S5MStarProductionAdapterError(
                "FX0_SINGLE_CASE_SOURCE_SEQUENCE_MUST_BE_ZERO"
            )

        source_hash = payload_sha256(case.source)
        source = MStarSource(
            source_sequence=case.source_sequence,
            source_sha256=source_hash,
            opaque_source=dict(case.source),
        )
        safe_case_id = re.sub(r"[^a-z0-9-]", "-", case.case_id.casefold()).strip("-")
        if not safe_case_id:
            raise S5MStarProductionAdapterError("FX0_CASE_ID_INVALID")
        run_id = f"s5-mstar-fx0-{safe_case_id}"
        spec = MStarSpec(
            run_id=run_id,
            production_core_identity_sha256=self.production_core_identity_sha256,
            prepare_concurrency=2,
            require_prepare_overlap=False,
        )
        observed: dict[str, object] = {}
        callback_failure: str | None = None

        async def prepare(opaque_source: object, logical_time_ns: int) -> object:
            if not isinstance(opaque_source, Mapping):
                raise S5MStarProductionAdapterError("FX0_SOURCE_INVALID")
            try:
                result = self.semantic_prepare(
                    opaque_source, logical_time_ns, providers
                )
                if not inspect.isawaitable(result):
                    raise TypeError("semantic_prepare must be async")
                return await result
            except S5MStarProductionAdapterError as error:
                nonlocal callback_failure
                callback_failure = error.error_code
                raise

        async def bind(
            prepared: object,
            logical_time_ns: int,
            source_sequence: int,
            visible_prefix: tuple[int, ...],
        ) -> object:
            try:
                result = self.latest_state_bind(
                    prepared,
                    logical_time_ns,
                    source_sequence,
                    visible_prefix,
                    providers,
                )
                if not inspect.isawaitable(result):
                    raise TypeError("latest_state_bind must be async")
                value = await result
                if isinstance(value, Mapping):
                    observed.update(deepcopy(dict(value)))
                return value
            except S5MStarProductionAdapterError as error:
                nonlocal callback_failure
                callback_failure = error.error_code
                raise

        evidence = await run_mstar_pipeline(
            spec=spec,
            sources=(source,),
            semantic_prepare=prepare,
            latest_state_bind=bind,
            persist_event=self.persist_event,
            clock_ns=self.clock_ns,
        )
        if evidence["status"] == "PASS":
            state, history = self.snapshot()
            if "canonical_logical_state" in observed:
                state = observed["canonical_logical_state"]
            if "publication_history" in observed:
                history = observed["publication_history"]
            error_code = None
            status = "PASS"
        else:
            state, history = self.snapshot()
            error_code = callback_failure or str(evidence["failure_code"])
            status = "FAIL_CLOSED"
        _assert_public(state)
        _assert_public(history)
        try:
            return MechanismOutcome(
                case_id=case.case_id,
                status=status,
                error_code=error_code,
                canonical_logical_state=state,
                publication_history=history,
            )
        except ValueError as error:
            raise S5MStarProductionAdapterError("FX0_OUTCOME_INVALID") from error


__all__ = [
    "S5MStarProductionAdapter",
    "S5MStarProductionAdapterError",
]
