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
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .artifacts import payload_sha256
from .fx0_mechanism_fixture import (
    ControlledNondeterminism,
    Fx0ExecutionCase,
    MechanismOutcome,
)
from .s5_mstar_pipeline import MStarSource, MStarSpec, run_mstar_pipeline
from .s5_mstar_production_core_identity import (
    S5MStarProductionCoreIdentityError,
    verify_s5_mstar_production_core_identity,
)


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
_REGISTERED_FX0_FAILURES = {
    "CONFLICTING_DUPLICATE_UUID",
    "LOST_PUBLICATION",
    "DUPLICATE_PUBLICATION",
    "PARTIAL_PUBLICATION",
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
    try:
        return verify_s5_mstar_production_core_identity(value)
    except S5MStarProductionCoreIdentityError:
        raise S5MStarProductionAdapterError("PRODUCTION_IDENTITY_INVALID") from None


@dataclass(frozen=True)
class Fx0DecodedSource:
    """One oracle-free source decoded for the shared M* core."""

    source_sha256: str
    opaque_source: object
    logical_time_ns: int

    def __post_init__(self) -> None:
        if not isinstance(self.source_sha256, str) or _SHA256.fullmatch(
            self.source_sha256
        ) is None:
            raise S5MStarProductionAdapterError("FX0_SOURCE_HASH_INVALID")
        if self.opaque_source is None:
            raise S5MStarProductionAdapterError("FX0_SOURCE_INVALID")
        if (
            isinstance(self.logical_time_ns, bool)
            or not isinstance(self.logical_time_ns, int)
            or self.logical_time_ns < 0
        ):
            raise S5MStarProductionAdapterError("FX0_LOGICAL_TIME_INVALID")


@dataclass(frozen=True)
class S5MStarFx0ExecutionEvidence:
    """Observed outcome plus sanitized shared-core evidence for one case."""

    outcome: MechanismOutcome
    pipeline_evidence: Mapping[str, object]
    source_count: int
    attempt_count: int
    execution_shape: Mapping[str, object]

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, MechanismOutcome):
            raise S5MStarProductionAdapterError("FX0_OUTCOME_INVALID")
        if not isinstance(self.pipeline_evidence, Mapping):
            raise S5MStarProductionAdapterError("FX0_EVIDENCE_INVALID")
        if isinstance(self.source_count, bool) or self.source_count < 1:
            raise S5MStarProductionAdapterError("FX0_SOURCE_COUNT_INVALID")
        if isinstance(self.attempt_count, bool) or self.attempt_count < 1:
            raise S5MStarProductionAdapterError("FX0_ATTEMPT_COUNT_INVALID")
        _assert_public(self.pipeline_evidence)
        _assert_public(self.execution_shape)
        object.__setattr__(
            self, "pipeline_evidence", deepcopy(dict(self.pipeline_evidence))
        )
        object.__setattr__(self, "execution_shape", deepcopy(dict(self.execution_shape)))


SemanticPrepare = Callable[
    [object, int, ControlledNondeterminism], Awaitable[object]
]
LatestStateBind = Callable[
    [object, int, int, tuple[int, ...], ControlledNondeterminism],
    Awaitable[object],
]
Snapshot = Callable[[], tuple[Mapping[str, Any], Sequence[Mapping[str, Any]]]]
PersistEvent = Callable[[Mapping[str, object]], Awaitable[object]]
ClockNs = Callable[[], int]
SourceDecoder = Callable[
    [Fx0ExecutionCase, ControlledNondeterminism], Sequence[Fx0DecodedSource]
]
CaseReset = Callable[[ControlledNondeterminism], Awaitable[object]]
WitnessSnapshot = Callable[[str], Mapping[str, bool]]
RecoverPublication = Callable[[MStarSource, int], Awaitable[object]]


class S5MStarProductionAdapter:
    """Bind real semantic callbacks to the shared M* scheduling core.

    ``execute_fixture_case`` receives only ``Fx0ExecutionCase`` and controlled
    providers.  Expected status, state, and history are intentionally absent
    from this interface; the FX0 comparator owns those values.
    """

    def __init__(
        self,
        *,
        production_core_identity: Mapping[str, object],
        production_core_identity_sha256: str,
        semantic_prepare: SemanticPrepare,
        latest_state_bind: LatestStateBind,
        snapshot: Snapshot,
        persist_event: PersistEvent | None = None,
        clock_ns: ClockNs = time.monotonic_ns,
        source_decoder: SourceDecoder | None = None,
        reset_case: CaseReset | None = None,
        witness_snapshot: WitnessSnapshot | None = None,
        recover_publication: RecoverPublication | None = None,
    ) -> None:
        self.production_core_identity = _identity(production_core_identity)
        if production_core_identity_sha256 != self.production_core_identity[
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
        self.persist_event_supplied = persist_event is not None
        self.clock_ns = clock_ns
        if source_decoder is not None and not callable(source_decoder):
            raise S5MStarProductionAdapterError("SOURCE_DECODER_NOT_CALLABLE")
        if reset_case is not None and not callable(reset_case):
            raise S5MStarProductionAdapterError("CASE_RESET_NOT_CALLABLE")
        if witness_snapshot is not None and not callable(witness_snapshot):
            raise S5MStarProductionAdapterError("WITNESS_SNAPSHOT_NOT_CALLABLE")
        if recover_publication is not None and not callable(recover_publication):
            raise S5MStarProductionAdapterError("RECOVER_PUBLICATION_NOT_CALLABLE")
        self.source_decoder = source_decoder or self._decode_single_source
        self.source_decoder_supplied = source_decoder is not None
        self.reset_case = reset_case
        self.witness_snapshot = witness_snapshot
        self.recover_publication = recover_publication
        self.production_path_identity = {
            "status": "FROZEN",
            "method": "M_STAR",
            "identity_sha256": production_core_identity_sha256,
        }

    @staticmethod
    async def _discard_event(_event: Mapping[str, object]) -> None:
        return None

    @staticmethod
    def _logical_time_ns(value: str) -> int:
        if not isinstance(value, str) or not value:
            raise S5MStarProductionAdapterError("FX0_LOGICAL_TIME_INVALID")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise S5MStarProductionAdapterError("FX0_LOGICAL_TIME_INVALID") from None
        if parsed.tzinfo is None:
            raise S5MStarProductionAdapterError("FX0_LOGICAL_TIME_INVALID")
        utc = parsed.astimezone(timezone.utc)
        return int(utc.timestamp() * 1_000_000_000)

    def _decode_single_source(
        self,
        case: Fx0ExecutionCase,
        providers: ControlledNondeterminism,
    ) -> tuple[Fx0DecodedSource, ...]:
        if len(providers.logical_times) != 1:
            raise S5MStarProductionAdapterError("FX0_LOGICAL_TIME_COUNT_INVALID")
        return (
            Fx0DecodedSource(
                source_sha256=payload_sha256(case.source),
                opaque_source=dict(case.source),
                logical_time_ns=self._logical_time_ns(providers.logical_times[0]),
            ),
        )

    async def execute_fixture_case(
        self,
        case: Fx0ExecutionCase,
        providers: ControlledNondeterminism,
    ) -> MechanismOutcome:
        """Execute one oracle-free FX0 case through the shared M* core."""

        execution = await self.execute_fixture_case_with_evidence(case, providers)
        return execution.outcome

    async def execute_fixture_case_with_evidence(
        self,
        case: Fx0ExecutionCase,
        providers: ControlledNondeterminism,
    ) -> S5MStarFx0ExecutionEvidence:
        """Execute one case and retain sanitized scheduling evidence."""

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

        if self.reset_case is not None:
            reset = self.reset_case(providers)
            if not inspect.isawaitable(reset):
                raise S5MStarProductionAdapterError("CASE_RESET_MUST_BE_ASYNC")
            await reset
        decoded = self.source_decoder(case, providers)
        if isinstance(decoded, (str, bytes)) or not isinstance(decoded, Sequence):
            raise S5MStarProductionAdapterError("FX0_DECODED_SOURCES_INVALID")
        selected = tuple(decoded)
        if not selected or any(not isinstance(item, Fx0DecodedSource) for item in selected):
            raise S5MStarProductionAdapterError("FX0_DECODED_SOURCES_INVALID")
        sources = tuple(
            MStarSource(
                source_sequence=index,
                source_sha256=item.source_sha256,
                opaque_source=item.opaque_source,
                logical_time_ns=item.logical_time_ns,
            )
            for index, item in enumerate(selected)
        )
        safe_case_id = re.sub(r"[^a-z0-9-]", "-", case.case_id.casefold()).strip("-")
        if not safe_case_id:
            raise S5MStarProductionAdapterError("FX0_CASE_ID_INVALID")
        run_id = f"s5-mstar-fx0-{safe_case_id}"
        spec = MStarSpec(
            run_id=run_id,
            production_core_identity_sha256=self.production_core_identity_sha256,
            prepare_concurrency=2,
            require_prepare_overlap=len(sources) > 1,
        )
        callback_failure: str | None = None
        recovery_count = 0

        async def prepare(opaque_source: object, logical_time_ns: int) -> object:
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
                _assert_public(value)
                return value
            except S5MStarProductionAdapterError as error:
                nonlocal callback_failure
                callback_failure = error.error_code
                raise

        async def recover(source: MStarSource, logical_time_ns: int) -> object:
            nonlocal recovery_count
            if self.recover_publication is None:
                raise S5MStarProductionAdapterError("PUBLICATION_RECOVERY_UNAVAILABLE")
            result = self.recover_publication(source, logical_time_ns)
            if not inspect.isawaitable(result):
                raise S5MStarProductionAdapterError("PUBLICATION_RECOVERY_NOT_ASYNC")
            value = await result
            recovery_count += 1
            return value

        evidence = await run_mstar_pipeline(
            spec=spec,
            sources=sources,
            semantic_prepare=prepare,
            latest_state_bind=bind,
            persist_event=self.persist_event,
            clock_ns=self.clock_ns,
            recover_publication=recover if self.recover_publication is not None else None,
        )
        if evidence["status"] == "PASS":
            state, history = self.snapshot()
            error_code = None
            status = "PASS"
        else:
            state, history = self.snapshot()
            if (
                callback_failure is not None
                and callback_failure not in _REGISTERED_FX0_FAILURES
            ):
                raise S5MStarProductionAdapterError(callback_failure)
            error_code = callback_failure or str(evidence["failure_code"])
            status = "FAIL_CLOSED"
        _assert_public(state)
        _assert_public(history)
        summary = evidence["summary"]
        published = summary["published_source_sequences"]
        shape: dict[str, object] = {
            "source_count": len(sources),
            "attempt_count": 1 + recovery_count,
            "prepare_overlap_observed": summary["prepare_overlap_observed"] is True,
            "published_source_count": len(published),
            "published_source_order_observed": published == list(range(len(published))),
            "prepare_to_bind_state_change_observed": False,
            "single_logical_publication_observed": len(published) == 1,
            "retry_replay_observed": recovery_count > 0,
            "publication_fault_detection_observed": recovery_count > 0,
        }
        if self.witness_snapshot is not None:
            witness = self.witness_snapshot(case.case_id)
            if not isinstance(witness, Mapping):
                raise S5MStarProductionAdapterError("FX0_WITNESS_INVALID")
            _assert_public(witness)
            allowed = {
                "prepare_to_bind_state_change_observed",
                "retry_replay_observed",
                "publication_fault_detection_observed",
            }
            if set(witness) - allowed:
                raise S5MStarProductionAdapterError("FX0_WITNESS_SHAPE_INVALID")
            for key, value in witness.items():
                if value is not True and value is not False:
                    raise S5MStarProductionAdapterError("FX0_WITNESS_VALUE_INVALID")
                shape[key] = value
        if shape["retry_replay_observed"] is True and recovery_count < 1:
            raise S5MStarProductionAdapterError("FX0_RETRY_WITNESS_WITHOUT_RECOVERY")
        try:
            outcome = MechanismOutcome(
                case_id=case.case_id,
                status=status,
                error_code=error_code,
                canonical_logical_state=state,
                publication_history=history,
            )
        except ValueError as error:
            raise S5MStarProductionAdapterError("FX0_OUTCOME_INVALID") from error
        return S5MStarFx0ExecutionEvidence(
            outcome=outcome,
            pipeline_evidence=evidence,
            source_count=len(sources),
            attempt_count=1 + recovery_count,
            execution_shape=shape,
        )


__all__ = [
    "Fx0DecodedSource",
    "S5MStarProductionAdapter",
    "S5MStarProductionAdapterError",
    "S5MStarFx0ExecutionEvidence",
]
