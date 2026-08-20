"""Measurement-only live composition for VDC capture and stale Probe evidence."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from paper_eval.membind_v31.live_block import V31LiveHooks, production_v31_live_hooks

from .capture import CapturedBindReplay
from .observation_adapter import (
    VDCExactReadObservation,
    VDCObservationAdapter,
    VDCPreparedObservation,
    VDCStaleProbeObservation,
)


class VDCObservationBundleError(ValueError):
    """A capture run emitted duplicate or malformed source evidence."""


def _fail(code: str) -> VDCObservationBundleError:
    return VDCObservationBundleError(code)


@dataclass(slots=True)
class VDCObservationBundle:
    captures: dict[int, CapturedBindReplay] = field(default_factory=dict)
    prepared: dict[int, VDCPreparedObservation] = field(default_factory=dict)
    stale_probes: dict[int, VDCStaleProbeObservation] = field(default_factory=dict)
    exact_reads: dict[int, VDCExactReadObservation] = field(default_factory=dict)

    def record_capture(self, value: CapturedBindReplay) -> None:
        if not isinstance(value, CapturedBindReplay):
            raise _fail("exact_capture_invalid")
        value.verify()
        sequence = value.prepared_artifact.source_sequence
        if sequence in self.captures:
            raise _fail("exact_capture_duplicate")
        self.captures[sequence] = value

    def record_prepared(self, value: VDCPreparedObservation) -> None:
        if not isinstance(value, VDCPreparedObservation):
            raise _fail("prepared_observation_invalid")
        if value.source_sequence in self.prepared:
            raise _fail("prepared_observation_duplicate")
        self.prepared[value.source_sequence] = value

    def record_stale_probe(self, value: VDCStaleProbeObservation) -> None:
        if not isinstance(value, VDCStaleProbeObservation):
            raise _fail("stale_probe_observation_invalid")
        if value.source_sequence in self.stale_probes:
            raise _fail("stale_probe_observation_duplicate")
        self.stale_probes[value.source_sequence] = value

    def record_exact_read(self, value: VDCExactReadObservation) -> None:
        if not isinstance(value, VDCExactReadObservation):
            raise _fail("exact_read_observation_invalid")
        if value.source_sequence in self.exact_reads:
            raise _fail("exact_read_observation_duplicate")
        self.exact_reads[value.source_sequence] = value


@dataclass(frozen=True, slots=True)
class VDCCaptureComposition:
    hooks: V31LiveHooks
    bundle: VDCObservationBundle
    execution_policy_changed: bool = False


def _production_factorized_adapter(runtime: object, certification: object) -> object:
    from paper_eval.membind_v4.live_block import (
        _production_factorized_adapter_factory,
    )

    return _production_factorized_adapter_factory(runtime, certification)


def build_vdc_capture_composition(
    *,
    bundle: VDCObservationBundle,
    base_hooks: V31LiveHooks | None = None,
    factorized_adapter_factory: Callable[[object, object], object] | None = None,
) -> VDCCaptureComposition:
    """Replace only the adapter with the exact capture/probe overlay."""

    if not isinstance(bundle, VDCObservationBundle):
        raise _fail("observation_bundle_invalid")
    selected_base = production_v31_live_hooks() if base_hooks is None else base_hooks
    if not isinstance(selected_base, V31LiveHooks):
        raise _fail("base_hooks_invalid")
    selected_factory = (
        _production_factorized_adapter
        if factorized_adapter_factory is None
        else factorized_adapter_factory
    )
    if not callable(selected_factory):
        raise _fail("factorized_adapter_factory_invalid")

    def adapter_factory(runtime: object, certification: object) -> object:
        try:
            factorized = selected_factory(runtime, certification)
        except Exception:
            raise _fail("factorized_adapter_construction_failed") from None
        return VDCObservationAdapter(
            factorized_adapter=factorized,
            capture_observer=bundle.record_capture,
            stale_probe_observer=bundle.record_stale_probe,
            prepared_observer=bundle.record_prepared,
            exact_read_observer=bundle.record_exact_read,
        )

    return VDCCaptureComposition(
        hooks=V31LiveHooks(
            runtime_builder=selected_base.runtime_builder,
            runtime_ready=selected_base.runtime_ready,
            namespace_probe=selected_base.namespace_probe,
            namespace_episode=selected_base.namespace_episode,
            source_visibility_probe=selected_base.source_visibility_probe,
            reference_time_to_ns=selected_base.reference_time_to_ns,
            adapter_factory=adapter_factory,
            close_runtime=selected_base.close_runtime,
        ),
        bundle=bundle,
        execution_policy_changed=False,
    )


__all__ = [
    "VDCCaptureComposition",
    "VDCObservationBundle",
    "VDCObservationBundleError",
    "build_vdc_capture_composition",
]

