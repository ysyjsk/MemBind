"""Q0 hook composition around the frozen v3.1 live envelope."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from paper_eval.membind_v31.admission import AdmissionPolicy
from paper_eval.membind_v31.certification import StateCutCertification
from paper_eval.membind_v31.graphiti_adapter import MemBindV31GraphitiAdapter
from paper_eval.membind_v31.live_block import V31LiveHooks, production_v31_live_hooks
from paper_eval.membind_v31.live_runtime import build_membind_v31_runtime
from paper_eval.s5_graphiti_semantic_binding import (
    S5GraphitiSemanticBinding,
    load_graphiti_semantic_binding,
)

from .instrumented_adapter import (
    MSEGInstrumentedAdapter,
    instrument_graphiti_semantic_binding,
)
from .observability import (
    MSEGOperatorTraceObserver,
    current_operator_metadata,
)


class Q0CompositionError(ValueError):
    """The passive Q0 composition cannot be built safely."""


def _fail(code: str) -> Q0CompositionError:
    return Q0CompositionError(code)


@dataclass(slots=True)
class Q0LiveComposition:
    hooks: V31LiveHooks
    observer: MSEGOperatorTraceObserver
    stream_id: str
    comparison_namespace: str | None
    comparison_state: dict[str, object] | None = None
    q0_namespace_snapshots: list[dict[str, object]] | None = None
    execution_policy_changed: bool = False
    runtime_method_identity: dict[str, object] | None = None


def build_q0_live_composition(
    *,
    observer: MSEGOperatorTraceObserver,
    stream_id: str,
    comparison_namespace: str | None = None,
    base_hooks: V31LiveHooks | None = None,
    runtime_builder: Callable[..., object] | None = None,
    semantic_binding_loader: Callable[[], S5GraphitiSemanticBinding]
    | None = None,
    inner_adapter_factory: Callable[
        [object, StateCutCertification, S5GraphitiSemanticBinding], object
    ]
    | None = None,
) -> Q0LiveComposition:
    """Compose only telemetry around production hooks; policy remains v3.1."""

    if not isinstance(observer, MSEGOperatorTraceObserver):
        raise _fail("operator_observer_invalid")
    if not isinstance(stream_id, str) or not stream_id:
        raise _fail("stream_id_invalid")
    selected_base = production_v31_live_hooks() if base_hooks is None else base_hooks
    if not isinstance(selected_base, V31LiveHooks):
        raise _fail("base_hooks_invalid")
    selected_runtime_builder = (
        build_membind_v31_runtime if runtime_builder is None else runtime_builder
    )
    if not callable(selected_runtime_builder):
        raise _fail("runtime_builder_invalid")
    selected_loader = (
        load_graphiti_semantic_binding
        if semantic_binding_loader is None
        else semantic_binding_loader
    )
    if not callable(selected_loader):
        raise _fail("semantic_binding_loader_invalid")

    composition: Q0LiveComposition

    def q0_runtime_builder(**kwargs: object) -> object:
        selected = dict(kwargs)
        if "causal_metadata_provider" in selected:
            raise _fail("causal_metadata_provider_reserved")
        selected["causal_metadata_provider"] = current_operator_metadata
        runtime = selected_runtime_builder(**selected)
        method_identity = getattr(runtime, "method_public_identity", None)
        if isinstance(method_identity, Mapping):
            composition.runtime_method_identity = deepcopy(dict(method_identity))
        return runtime

    async def q0_runtime_ready(runtime: object) -> object:
        result = await selected_base.runtime_ready(runtime)
        if comparison_namespace is not None:
            if not isinstance(comparison_namespace, str) or not comparison_namespace:
                raise _fail("comparison_namespace_invalid")
            state = await selected_base.namespace_probe(runtime, comparison_namespace)
            if not isinstance(state, Mapping):
                raise _fail("comparison_state_invalid")
            composition.comparison_state = deepcopy(dict(state))
        return result

    async def q0_namespace_probe(runtime: object, namespace: str) -> object:
        value = await selected_base.namespace_probe(runtime, namespace)
        if not isinstance(value, Mapping):
            raise _fail("namespace_probe_result_invalid")
        if composition.q0_namespace_snapshots is None:
            composition.q0_namespace_snapshots = []
        composition.q0_namespace_snapshots.append(deepcopy(dict(value)))
        return value

    def default_inner_adapter_factory(
        runtime: object,
        certification: StateCutCertification,
        binding: S5GraphitiSemanticBinding,
    ) -> object:
        from graphiti_core.edges import EntityEdge
        from graphiti_core.nodes import EntityNode, EpisodeType, EpisodicNode

        from paper_eval.membind_v1.graphiti_factories import make_graphiti_node_factories

        graphiti = getattr(runtime, "graphiti")
        factories = make_graphiti_node_factories(
            episodic_node_type=EpisodicNode,
            entity_node_type=EntityNode,
            message_source=EpisodeType.message,
        )
        return MemBindV31GraphitiAdapter(
            graphiti=graphiti,
            llm_client=getattr(graphiti, "llm_client"),
            semantic_binding=binding,
            episode_factory=factories.episode_factory,
            extracted_node_factory=factories.extracted_node_factory,
            extracted_edge_factory=lambda value: EntityEdge(**dict(value)),
            state_cut_certification=certification,
        )

    selected_inner_factory = (
        default_inner_adapter_factory
        if inner_adapter_factory is None
        else inner_adapter_factory
    )
    if not callable(selected_inner_factory):
        raise _fail("inner_adapter_factory_invalid")

    def q0_adapter_factory(runtime: object, certification: StateCutCertification) -> object:
        binding = instrument_graphiti_semantic_binding(selected_loader())
        inner = selected_inner_factory(runtime, certification, binding)
        return MSEGInstrumentedAdapter(
            inner=inner,
            stream_id=stream_id,
            observer=observer,
        )

    composition = Q0LiveComposition(
        hooks=V31LiveHooks(
            runtime_builder=q0_runtime_builder,
            runtime_ready=q0_runtime_ready,
            namespace_probe=q0_namespace_probe,
            namespace_episode=selected_base.namespace_episode,
            source_visibility_probe=selected_base.source_visibility_probe,
            reference_time_to_ns=selected_base.reference_time_to_ns,
            adapter_factory=q0_adapter_factory,
            close_runtime=selected_base.close_runtime,
        ),
        observer=observer,
        stream_id=stream_id,
        comparison_namespace=comparison_namespace,
        q0_namespace_snapshots=[],
    )
    return composition


__all__ = ["Q0CompositionError", "Q0LiveComposition", "build_q0_live_composition"]
