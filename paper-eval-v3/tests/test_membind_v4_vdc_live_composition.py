"""TDD contracts for the measurement-only VDC live composition."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from paper_eval.membind_v31.live_block import V31LiveHooks
from paper_eval.membind_v4.vdc.live_composition import (
    VDCObservationBundle,
    VDCObservationBundleError,
    build_vdc_capture_composition,
)
from paper_eval.membind_v4.vdc.observation_adapter import VDCObservationAdapter


def _base_hooks() -> V31LiveHooks:
    async def async_value(*_args, **_kwargs):
        return None

    return V31LiveHooks(
        runtime_builder=lambda **kwargs: {"runtime": kwargs},
        runtime_ready=async_value,
        namespace_probe=async_value,
        namespace_episode=lambda episode, _namespace: episode,
        source_visibility_probe=async_value,
        reference_time_to_ns=lambda _value: 0,
        adapter_factory=lambda _runtime, _certification: object(),
        close_runtime=async_value,
    )


class _Factorized:
    async def prepare(self, compile_input):
        return compile_input

    def v4_node_resolve_callbacks(self):
        async def callback(*_args, **_kwargs):
            return None

        return {
            "materialize_request": callback,
            "execute_request": callback,
            "interpret_response": callback,
            "continue_native_bind": callback,
        }


def test_composition_changes_only_adapter_factory() -> None:
    base = _base_hooks()
    bundle = VDCObservationBundle()
    composition = build_vdc_capture_composition(
        bundle=bundle,
        base_hooks=base,
        factorized_adapter_factory=lambda _runtime, _certification: _Factorized(),
    )

    assert composition.hooks.runtime_builder is base.runtime_builder
    assert composition.hooks.runtime_ready is base.runtime_ready
    assert composition.hooks.namespace_probe is base.namespace_probe
    assert composition.hooks.namespace_episode is base.namespace_episode
    assert composition.hooks.source_visibility_probe is base.source_visibility_probe
    assert composition.hooks.reference_time_to_ns is base.reference_time_to_ns
    assert composition.hooks.close_runtime is base.close_runtime
    assert isinstance(
        composition.hooks.adapter_factory(SimpleNamespace(), SimpleNamespace()),
        VDCObservationAdapter,
    )
    assert composition.execution_policy_changed is False


def test_bundle_rejects_duplicate_exact_capture_source() -> None:
    bundle = VDCObservationBundle()
    marker = SimpleNamespace(
        prepared_artifact=SimpleNamespace(source_sequence=1),
        verify=lambda: None,
    )
    with pytest.raises(VDCObservationBundleError, match="exact_capture_invalid"):
        bundle.record_capture(marker)

