"""Durable M* production-runner composition, still gated by FX0 authority."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path

from .s5_durable_attempt_store import S5AttemptStore
from .s5_mstar_pipeline import MStarSource, MStarSpec, run_mstar_pipeline
from .s5_production_runner import (
    S5ProductionIdentityError,
    verify_s5_production_identity,
)


class S5MStarProductionRunnerError(ValueError):
    """M* runner composition or identity failure."""


SemanticPrepare = Callable[[object, int], Awaitable[object]]
LatestStateBind = Callable[[object, int, int, tuple[int, ...]], Awaitable[object]]
ClockNs = Callable[[], int]


class S5MStarProductionRunner:
    """Compose pinned M* scheduling, semantic callbacks, and durable evidence.

    The caller supplies the Graphiti semantic callbacks.  This class does not
    create Graphiti, load environment variables, or infer live authority from
    an identity hash; the M* FX0 parity artifact must already be bound into the
    identity before construction is accepted.
    """

    def __init__(
        self,
        *,
        attempt_root: Path,
        spec: MStarSpec,
        identity: Mapping[str, object],
        sources: Sequence[MStarSource],
        semantic_prepare: SemanticPrepare,
        latest_state_bind: LatestStateBind,
        clock_ns: ClockNs,
    ) -> None:
        if not isinstance(spec, MStarSpec):
            raise S5MStarProductionRunnerError("mstar_spec_invalid")
        try:
            checked = verify_s5_production_identity(identity)
        except S5ProductionIdentityError as error:
            raise S5MStarProductionRunnerError(str(error)) from None
        if checked["method"] != "M*":
            raise S5MStarProductionRunnerError("identity_method_mismatch")
        if spec.production_core_identity_sha256 != checked["identity_sha256"]:
            raise S5MStarProductionRunnerError("production_core_identity_mismatch")
        selected = tuple(sources)
        if (
            len(selected) < 2
            or any(not isinstance(item, MStarSource) for item in selected)
            or [item.source_sequence for item in selected] != list(range(len(selected)))
        ):
            raise S5MStarProductionRunnerError("sources_invalid")
        if not callable(semantic_prepare) or not callable(latest_state_bind):
            raise S5MStarProductionRunnerError("semantic_callback_invalid")
        if not callable(clock_ns):
            raise S5MStarProductionRunnerError("clock_invalid")
        root = Path(attempt_root)
        if root.exists():
            raise S5MStarProductionRunnerError("attempt_exists")
        self.attempt_root = root
        self.spec = spec
        self.identity = checked
        self.sources = selected
        self.semantic_prepare = semantic_prepare
        self.latest_state_bind = latest_state_bind
        self.clock_ns = clock_ns

    async def run(self) -> dict[str, object]:
        store = S5AttemptStore.create(
            self.attempt_root,
            run_id=self.spec.run_id,
            method="M*",
            production_core_identity_sha256=self.identity["identity_sha256"],
            source_sha256s=tuple(item.source_sha256 for item in self.sources),
        )

        async def persist_event(event: Mapping[str, object]) -> None:
            store.append_event(event)

        evidence = await run_mstar_pipeline(
            spec=self.spec,
            sources=self.sources,
            semantic_prepare=self.semantic_prepare,
            latest_state_bind=self.latest_state_bind,
            persist_event=persist_event,
            clock_ns=self.clock_ns,
        )
        finalized = store.finalize(evidence)
        return {
            **finalized,
            "payload": evidence,
            "production_identity_sha256": self.identity["identity_sha256"],
        }


__all__ = ["S5MStarProductionRunner", "S5MStarProductionRunnerError"]
