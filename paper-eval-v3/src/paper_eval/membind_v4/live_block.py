"""Production composition for a MemBind v4 live block.

The v3.1 live block owns the durable source log, fresh-namespace probes,
State-Cut checks, lifecycle persistence, and the coordinator.  This module
only composes those unchanged pieces with the v4 residual-slot observer and
factorized NodeResolve facade.  A factorized Graphiti surface is deliberately
injected: when the pinned adapter does not expose it, live execution fails
closed before a candidate result can be produced.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from paper_eval.artifacts import atomic_write_json, payload_sha256
from paper_eval.apc_aligned_baseline import APC_BASELINE_HISTORIES, build_apc_aligned_baseline_plan
from paper_eval.membind_v31.certification import StateCutCertification
from paper_eval.membind_v31.freezer import V31FreezePaths, load_v31_state_cut_certification
from paper_eval.membind_v31.live_block import (
    V31LiveHooks,
    execute_v31_live_block,
    production_v31_live_hooks,
)
from paper_eval.membind_v31.artifacts import inspect_v31_block
from paper_eval.membind_v31.method_plan import (
    build_membind_v31_live_plan,
    verify_membind_v31_method_plan,
)
from paper_eval.membind_v31.production_executor import (
    ProductionExecutorPaths,
    _default_control_plan,
    _default_env_loader,
    _default_episode_builder,
    load_development_episodes,
)
from paper_eval.membind_v4.live_adapter import V4LiveNodeResolveError
from paper_eval.membind_v4.freeze import FORMAL_HISTORY_IDS, verify_frozen_method
from paper_eval.membind_v4.speculative_adapter import (
    V4ResidualSlotSignal,
    V4SpeculativeGraphitiAdapter,
)


def _public_request_projection(value: object) -> object:
    """Hash request shape while excluding prompt/response contents."""

    if isinstance(value, Mapping):
        projected: dict[str, object] = {}
        for key, child in sorted(value.items(), key=lambda item: str(item[0])):
            if str(key).casefold() in {"content", "prompt", "response", "messages"}:
                if key == "messages" and isinstance(child, Sequence) and not isinstance(child, (str, bytes)):
                    projected[str(key)] = [
                        {
                            "role": item.get("role") if isinstance(item, Mapping) else getattr(item, "role", None),
                            "content_sha256": hashlib.sha256(
                                str(item.get("content") if isinstance(item, Mapping) else getattr(item, "content", "")).encode("utf-8")
                            ).hexdigest(),
                        }
                        for item in child
                    ]
                else:
                    projected[str(key) + "_sha256"] = hashlib.sha256(
                        str(child).encode("utf-8")
                    ).hexdigest()
            else:
                projected[str(key)] = _public_request_projection(child)
        return projected
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_public_request_projection(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    schema = getattr(value, "model_json_schema", None)
    if callable(schema):
        return {"model_schema": schema()}
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            return _public_request_projection(dump(mode="json"))
        except TypeError:
            return _public_request_projection(dump())
    return {"type": f"{type(value).__module__}.{type(value).__qualname__}"}


def _build_graphiti_semantic_encoder(
    runtime: object,
    *,
    multilingual_instruction: Callable[[str | None], str],
) -> Callable[[object], Mapping[str, object]]:
    """Recreate the exact pre-transport Graphiti token projection."""

    from paper_eval.membind_v31.prefix_affinity import QwenGraphitiPrefixEncoder

    if not callable(multilingual_instruction):
        raise _fail("multilingual_instruction_unavailable")
    admitted = getattr(runtime, "admitted_llm", None)
    transport_encoder = getattr(admitted, "_prefix_encoder", None)
    raw_llm = getattr(runtime, "raw_llm", None)
    if raw_llm is None:
        raw_llm = getattr(getattr(runtime, "graphiti", None), "llm_client", None)
    fields = {
        "tokenizer": getattr(transport_encoder, "_tokenizer", None),
        "prefix_match_unit": getattr(transport_encoder, "_unit", None),
        "tokenizer_identity_sha256": getattr(transport_encoder, "_identity", None),
        "cache_identity_sha256": getattr(transport_encoder, "_cache_identity", None),
        "trace_hmac_key": getattr(transport_encoder, "_trace_hmac_key", None),
    }
    try:
        graphiti_encoder = QwenGraphitiPrefixEncoder(
            inner=raw_llm,
            multilingual_instruction=multilingual_instruction,
            **fields,
        )
    except Exception:
        raise _fail("factorized_prefix_encoder_unavailable") from None

    def semantic_encoder(captured: object) -> Mapping[str, object]:
        args = tuple(getattr(captured, "args", ()))
        raw_kwargs = getattr(captured, "kwargs", None)
        if not isinstance(raw_kwargs, Mapping):
            raise _fail("factorized_request_capture_invalid")
        kwargs = dict(raw_kwargs)
        try:
            metadata = graphiti_encoder(*deepcopy(args), **deepcopy(kwargs))
            metadata.verify()
        except Exception:
            raise _fail("factorized_prefix_identity_failed") from None
        rendered = hashlib.sha256(
            json.dumps(
                _public_request_projection({"args": args, "kwargs": kwargs}),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()
        return {
            "rendered_request_sha256": rendered,
            "token_sequence_sha256": metadata.token_sequence_hmac_sha256,
            "prompt_tokens": metadata.token_count,
        }

    # Keep the non-secret tokenizer/cache identity discoverable by the
    # production composition so it can participate in the semantic-call
    # fingerprint as an operator input.
    setattr(
        semantic_encoder,
        "public_identity",
        deepcopy(getattr(graphiti_encoder, "public_identity")),
    )
    return semantic_encoder


def _production_factorized_adapter_factory(
    runtime: object, certification: StateCutCertification
) -> object:
    """Wrap the pinned v3.1 adapter with the real Graphiti 0.29.3 split."""

    from graphiti_core.llm_client.openai_generic_client import (
        get_extraction_language_instruction,
    )
    from graphiti_core.prompts.dedupe_nodes import NodeResolutions
    from paper_eval.membind_v31.live_block import production_v31_live_hooks
    from paper_eval.s5_graphiti_semantic_binding import load_graphiti_semantic_binding
    from paper_eval.membind_v4.graphiti_factorization import V4GraphitiFactorizedAdapter

    base = production_v31_live_hooks()
    native = base.adapter_factory(runtime, certification)
    graphiti = getattr(native, "_graphiti", None)
    llm = getattr(graphiti, "llm_client", None)
    semantic_binding = load_graphiti_semantic_binding()
    semantic_encoder = _build_graphiti_semantic_encoder(
        runtime,
        multilingual_instruction=get_extraction_language_instruction,
    )
    identity = _build_production_identity_metadata(
        runtime=runtime,
        llm=llm,
        semantic_binding_identity_sha256=semantic_binding.identity_sha256(),
        response_schema=NodeResolutions.model_json_schema(),
    )
    return V4GraphitiFactorizedAdapter(
        native,
        semantic_encoder=semantic_encoder,
        identity_metadata=identity,
    )


class V4LiveBlockError(ValueError):
    """A v4 production composition or loader failed closed."""


def _fail(code: str) -> V4LiveBlockError:
    return V4LiveBlockError(code)


def _build_production_identity_metadata(
    *,
    runtime: object,
    llm: object,
    semantic_binding_identity_sha256: str,
    response_schema: Mapping[str, object],
) -> dict[str, object]:
    """Bind the factorized adapter to the actual pinned Graphiti client config.

    The public v3.1 envelope records the intended model, endpoint, and token
    cap.  The live client is checked independently so a wrapper that silently
    drops a config field cannot create a weaker semantic identity.
    """

    shared_identity = getattr(runtime, "shared_public_identity", None)
    construction = (
        shared_identity.get("construction")
        if isinstance(shared_identity, Mapping)
        else None
    )
    if not isinstance(construction, Mapping):
        raise _fail("factorized_construction_identity_missing")
    expected_model = construction.get("served_model_id")
    expected_base_url = construction.get("base_url")
    expected_max_tokens = construction.get("requested_max_tokens")
    if (
        not isinstance(expected_model, str)
        or not expected_model
        or not isinstance(expected_base_url, str)
        or not expected_base_url
        or isinstance(expected_max_tokens, bool)
        or not isinstance(expected_max_tokens, int)
        or expected_max_tokens <= 0
    ):
        raise _fail("factorized_construction_identity_invalid")
    config = getattr(llm, "config", None)
    actual_model = getattr(llm, "model", None)
    if actual_model is None and config is not None:
        actual_model = getattr(config, "model", None)
    actual_base_url = getattr(config, "base_url", None)
    if actual_base_url is None:
        actual_base_url = getattr(llm, "base_url", None)
    actual_max_tokens = getattr(llm, "max_tokens", None)
    actual_temperature = getattr(llm, "temperature", None)
    if actual_temperature is None and config is not None:
        actual_temperature = getattr(config, "temperature", None)
    actual_structured_mode = getattr(llm, "structured_output_mode", None)
    transport = getattr(llm, "client", None)
    chat = getattr(transport, "chat", None)
    completions = getattr(chat, "completions", None)
    structured_backend = getattr(completions, "_structured_backend_identity", None)
    if (
        actual_model != expected_model
        or actual_base_url != expected_base_url
        or actual_max_tokens != expected_max_tokens
        or actual_temperature != 0.0
        or actual_structured_mode != "json_schema"
        or structured_backend != "xgrammar"
    ):
        raise _fail("factorized_llm_identity_mismatch")
    if not isinstance(semantic_binding_identity_sha256, str) or len(semantic_binding_identity_sha256) != 64:
        raise _fail("semantic_binding_identity_invalid")
    if not isinstance(response_schema, Mapping):
        raise _fail("response_schema_invalid")
    admitted = getattr(runtime, "admitted_llm", None)
    prefix_encoder = getattr(admitted, "_prefix_encoder", None)
    prefix_identity = getattr(prefix_encoder, "public_identity", None)
    if callable(prefix_identity):
        prefix_identity = prefix_identity()
    if not isinstance(prefix_identity, Mapping):
        raise _fail("factorized_prefix_identity_missing")
    prefix_identity = dict(prefix_identity)
    required_prefix_fields = (
        "schema_version",
        "tokenizer_identity_sha256",
        "trace_key_identity_sha256",
        "cache_identity_sha256",
        "prefix_match_unit",
    )
    if any(field not in prefix_identity for field in required_prefix_fields):
        raise _fail("factorized_prefix_identity_invalid")
    return {
        "operator_identity": {
            "graphiti_version": "0.29.3",
            "semantic_binding_identity_sha256": semantic_binding_identity_sha256,
            "node_operations_module": "graphiti_core.utils.maintenance.node_operations",
            "prefix_encoder_identity": prefix_identity,
        },
        "model_identity": dict(construction),
        "decoding_identity": {
            "model": actual_model,
            "base_url": actual_base_url,
            "structured_output_mode": actual_structured_mode,
            "structured_backend_identity": structured_backend,
            "max_tokens": actual_max_tokens,
            "temperature": actual_temperature,
            "boundary": "graphiti_core.llm_client.openai_generic_client.OpenAIGenericClient",
        },
        "response_schema": dict(response_schema),
        "operator_revision": "graphiti-0.29.3-node-resolve-v4-1",
    }


async def _await(value: object, code: str) -> object:
    if not inspect.isawaitable(value):
        raise _fail(code)
    return await value


def _supports_keyword(callback: object, name: str) -> bool:
    if not callable(callback):
        return False
    try:
        parameters = inspect.signature(callback).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == name or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def _per_source_freshness(events: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Project content-safe arrival/publication rows for every source."""

    arrivals: dict[int, int] = {}
    publications: dict[int, int] = {}
    for event in events:
        if not isinstance(event, Mapping):
            raise _fail("lifecycle_event_invalid")
        sequence = event.get("source_sequence")
        timestamp = event.get("timestamp_ns")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise _fail("lifecycle_source_invalid")
        if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp < 0:
            raise _fail("lifecycle_timestamp_invalid")
        event_type = event.get("event_type")
        if event_type == "ARRIVAL":
            arrivals[sequence] = timestamp
        elif event_type == "PUBLICATION_DURABLE":
            publications[sequence] = timestamp
    if set(arrivals) != set(publications) or not arrivals:
        raise _fail("freshness_coverage_incomplete")
    rows: list[dict[str, object]] = []
    freshness: list[int] = []
    for sequence in sorted(arrivals):
        arrival = arrivals[sequence]
        publication = publications[sequence]
        if publication < arrival:
            raise _fail("freshness_order_invalid")
        value = publication - arrival
        freshness.append(value)
        rows.append(
            {
                "source_sequence": sequence,
                "arrival_timestamp_ns": arrival,
                "publication_timestamp_ns": publication,
                "freshness_ns": value,
            }
        )
    return {"freshness_ns": freshness, "per_source": rows}


@dataclass(frozen=True, slots=True)
class V4LiveBlockComposition:
    """Hooks plus the last public v4 telemetry captured at runtime close."""

    hooks: V31LiveHooks
    telemetry: Callable[[], Mapping[str, object]]


def build_v4_live_composition(
    *,
    stream_id: str,
    base_hooks: V31LiveHooks | None = None,
    factorized_adapter_factory: Callable[[object, StateCutCertification], object] | None = None,
) -> V4LiveBlockComposition:
    """Compose one stream's v4 hooks around the unchanged v3.1 hooks.

    ``stream_id`` is bound when the composition is built, so every speculative
    request is classified against the same history.  The runtime builder must
    accept ``admission_observer``; silently dropping it would make the
    residual-slot proof unverifiable and is therefore rejected.
    """

    if not isinstance(stream_id, str) or not stream_id:
        raise _fail("stream_id_invalid")
    selected = production_v31_live_hooks() if base_hooks is None else base_hooks
    if not isinstance(selected, V31LiveHooks):
        raise _fail("base_live_hooks_invalid")
    if not _supports_keyword(selected.runtime_builder, "admission_observer"):
        raise _fail("runtime_builder_admission_observer_unavailable")
    if factorized_adapter_factory is None and base_hooks is None:
        native_factory = _production_factorized_adapter_factory
    else:
        native_factory = selected.adapter_factory if factorized_adapter_factory is None else factorized_adapter_factory
    if not callable(native_factory):
        raise _fail("factorized_adapter_factory_invalid")

    signals: dict[int, V4ResidualSlotSignal] = {}
    adapters: dict[int, V4SpeculativeGraphitiAdapter] = {}
    last_telemetry: dict[str, object] = {}

    def runtime_builder(**kwargs: object) -> object:
        if "admission_observer" in kwargs:
            raise _fail("admission_observer_reserved")
        signal = V4ResidualSlotSignal()
        selected_kwargs = dict(kwargs)
        selected_kwargs["admission_observer"] = signal.observe
        runtime = selected.runtime_builder(**selected_kwargs)
        if inspect.isawaitable(runtime):
            raise _fail("runtime_builder_must_be_synchronous")
        signals[id(runtime)] = signal
        return runtime

    def adapter_factory(runtime: object, certification: StateCutCertification) -> object:
        signal = signals.get(id(runtime))
        if signal is None:
            raise _fail("runtime_signal_missing")
        try:
            native = native_factory(runtime, certification)
            facade = V4SpeculativeGraphitiAdapter(
                factorized_adapter=native,
                residual_slot_signal=signal,
                stream_id=stream_id,
            )
        except V4LiveNodeResolveError:
            raise
        except Exception as error:
            raise _fail(f"factorized_adapter_unavailable:{type(error).__qualname__}") from None
        adapters[id(runtime)] = facade
        return facade

    async def close_runtime(runtime: object) -> None:
        facade = adapters.pop(id(runtime), None)
        try:
            if facade is not None:
                await facade.close()
                last_telemetry.clear()
                last_telemetry.update(facade.telemetry())
        finally:
            signals.pop(id(runtime), None)
            await _await(selected.close_runtime(runtime), "runtime_close_must_be_async")

    hooks = V31LiveHooks(
        runtime_builder=runtime_builder,
        runtime_ready=selected.runtime_ready,
        namespace_probe=selected.namespace_probe,
        namespace_episode=selected.namespace_episode,
        source_visibility_probe=selected.source_visibility_probe,
        reference_time_to_ns=selected.reference_time_to_ns,
        adapter_factory=adapter_factory,
        close_runtime=close_runtime,
    )
    return V4LiveBlockComposition(hooks=hooks, telemetry=lambda: dict(last_telemetry))


def build_v4_live_hooks(
    *,
    stream_id: str,
    base_hooks: V31LiveHooks | None = None,
    factorized_adapter_factory: Callable[[object, StateCutCertification], object] | None = None,
) -> V31LiveHooks:
    """Return only the v3.1-compatible hooks for callers that need that API."""

    return build_v4_live_composition(
        stream_id=stream_id,
        base_hooks=base_hooks,
        factorized_adapter_factory=factorized_adapter_factory,
    ).hooks


# Naming aliases make the composition discoverable beside the v3.1
# ``production_v31_live_hooks`` entry point without introducing a second path.
build_v4_live_block_hooks = build_v4_live_hooks


def production_v4_live_hooks(*, stream_id: str) -> V31LiveHooks:
    """Build the default production hooks for one history stream."""

    return build_v4_live_hooks(stream_id=stream_id)


async def execute_v4_live_block(
    *,
    verified_plan: Mapping[str, object],
    block_index: int,
    episodes: Sequence[object],
    env: Mapping[str, str],
    block_root: Path,
    state_cut_certification: StateCutCertification,
    compile_workers: int,
    lookahead: int,
    stream_id: str | None = None,
    namespace_override: str | None = None,
    base_hooks: V31LiveHooks | None = None,
    factorized_adapter_factory: Callable[[object, StateCutCertification], object] | None = None,
) -> dict[str, object]:
    """Run one production v4 block through the unchanged v3.1 coordinator."""

    try:
        plan = verify_membind_v31_method_plan(verified_plan)
    except ValueError:
        raise _fail("verified_plan_invalid") from None
    if isinstance(block_index, bool) or not isinstance(block_index, int) or not 0 <= block_index < len(plan["blocks"]):
        raise _fail("block_index_invalid")
    selected_block = plan["blocks"][block_index]
    selected_stream = selected_block["history_id"] if stream_id is None else stream_id
    if selected_stream != selected_block["history_id"]:
        raise _fail("stream_id_plan_mismatch")
    composition = build_v4_live_composition(
        stream_id=selected_stream,
        base_hooks=base_hooks,
        factorized_adapter_factory=factorized_adapter_factory,
    )
    result = await execute_v31_live_block(
        verified_plan=plan,
        block_index=block_index,
        episodes=episodes,
        env=env,
        block_root=Path(block_root),
        state_cut_certification=state_cut_certification,
        compile_workers=compile_workers,
        lookahead=lookahead,
        hooks=composition.hooks,
        namespace_override=namespace_override,
    )
    telemetry = dict(composition.telemetry())
    if not telemetry or telemetry.get("persistent_write_count") != 0:
        raise _fail("speculative_persistent_write")
    inspected = inspect_v31_block(Path(block_root))
    performance = deepcopy(result.get("performance"))
    if not isinstance(performance, Mapping):
        performance = {}
    performance = {**dict(performance), **_per_source_freshness(inspected["events"])}
    artifact = {
        "schema_version": "membind.paper-eval-v4.live-block-result.v1",
        "status": result.get("status"),
        "run_id": result.get("run_id"),
        "block_index": block_index,
        "history_id": selected_stream,
        "namespace": result.get("namespace"),
        "source_count": result.get("source_count"),
        "direct_violation_count": result.get("direct_violation_count"),
        "performance": performance,
        "admission_observation": deepcopy(result.get("request_admission")),
        "telemetry": telemetry,
        "v31_result_payload_sha256": result.get("payload_sha256"),
    }
    artifact["payload_sha256"] = payload_sha256(artifact)
    atomic_write_json(Path(block_root) / "V4_BLOCK_RESULT.json", artifact)
    return artifact


@dataclass(frozen=True, slots=True)
class V4ProductionLoaders:
    """Dependency-injected production loaders, mirroring v3.1 exactly."""

    load_plan: Callable[[Path], Mapping[str, object]]
    load_env: Callable[[Path], Mapping[str, str]]
    load_certification: Callable[[V31FreezePaths], StateCutCertification]
    load_episodes: Callable[[Path, Mapping[str, object]], Mapping[str, Sequence[object]]]


def production_v4_loaders(paths: ProductionExecutorPaths) -> V4ProductionLoaders:
    """Build loaders from the existing sealed v3.1 production inputs."""

    if not isinstance(paths, ProductionExecutorPaths):
        raise _fail("production_paths_invalid")
    builder = _default_episode_builder(paths.legacy_root)
    return V4ProductionLoaders(
        load_plan=_default_control_plan,
        load_env=_default_env_loader,
        load_certification=lambda freeze_paths: load_v31_state_cut_certification(freeze_paths),
        load_episodes=lambda path, plan: load_development_episodes(
            development_input=path,
            verified_plan=plan,
            episode_builder=builder,
        ),
    )


def build_v4_production_block_runner(
    *,
    paths: ProductionExecutorPaths | None = None,
    loaders: V4ProductionLoaders | None = None,
    base_hooks_factory: Callable[[], V31LiveHooks] | None = None,
    factorized_adapter_factory: Callable[[object, StateCutCertification], object] | None = None,
) -> Callable[..., Mapping[str, object]]:
    """Build a synchronous runner suitable for v3.1/v4 orchestration hooks.

    Context is loaded once on first invocation.  The returned function accepts
    ``(supplied_plan, block_index, root)`` exactly like the v3.1 production
    executor and therefore keeps all source identity and State-Cut loaders in
    one place.
    """

    selected_paths = ProductionExecutorPaths.from_repository(Path(__file__).resolve().parents[4]) if paths is None else paths
    if not isinstance(selected_paths, ProductionExecutorPaths):
        raise _fail("production_paths_invalid")
    selected_loaders = production_v4_loaders(selected_paths) if loaders is None else loaders
    if not isinstance(selected_loaders, V4ProductionLoaders):
        raise _fail("production_loaders_invalid")
    canonical = verify_membind_v31_method_plan(selected_loaders.load_plan(selected_paths.control_root))
    loaded: dict[str, object] = {}

    def context() -> tuple[Mapping[str, str], StateCutCertification, Mapping[str, Sequence[object]]]:
        if not loaded:
            env = selected_loaders.load_env(selected_paths.env_file)
            cert = selected_loaders.load_certification(selected_paths.freeze_paths)
            episodes = selected_loaders.load_episodes(selected_paths.development_input, canonical)
            if not isinstance(env, Mapping) or not isinstance(cert, StateCutCertification) or tuple(episodes) != tuple(canonical["histories"]):
                raise _fail("production_context_invalid")
            loaded.update(env=dict(env), certification=cert, episodes=episodes)
        return loaded["env"], loaded["certification"], loaded["episodes"]  # type: ignore[return-value]

    def run_block(supplied_plan: Mapping[str, object], block_index: int, root: Path) -> Mapping[str, object]:
        try:
            selected_plan = verify_membind_v31_method_plan(supplied_plan)
        except ValueError:
            raise _fail("formal_plan_invalid") from None
        if selected_plan != canonical:
            raise _fail("formal_plan_binding_invalid")
        env, cert, episodes = context()
        if isinstance(block_index, bool) or not isinstance(block_index, int) or not 0 <= block_index < len(canonical["blocks"]):
            raise _fail("formal_block_index_invalid")
        block = canonical["blocks"][block_index]
        hooks = base_hooks_factory() if base_hooks_factory is not None else None
        return asyncio.run(
            execute_v4_live_block(
                verified_plan=canonical,
                block_index=block_index,
                episodes=episodes[block["history_id"]],
                env=env,
                block_root=Path(root),
                state_cut_certification=cert,
                compile_workers=int(block["compile_workers"]),
                lookahead=int(block["lookahead"]),
                base_hooks=hooks,
                factorized_adapter_factory=factorized_adapter_factory,
            )
        )

    return run_block


def _fresh_v31_plan(
    canonical: Mapping[str, object],
    *,
    full_history_run_id: str,
    namespace: str,
    frozen_method_sha256: str,
) -> dict[str, object]:
    """Derive a fresh, fully verified v3.1 plan for one formal history."""

    try:
        baseline = build_apc_aligned_baseline_plan(
            run_id=canonical["baseline_run_id"],  # type: ignore[arg-type]
            history_source_sha256s={
                history: canonical["history_source_sha256s"][history]  # type: ignore[index]
                for history in APC_BASELINE_HISTORIES
            },
            interarrival_ns=canonical["interarrival_ns"],  # type: ignore[arg-type]
            execution_envelope_sha256=canonical["shared_execution_envelope_sha256"],  # type: ignore[arg-type]
            service_reference_ns=canonical["service_reference_ns"],  # type: ignore[arg-type]
            normalized_offered_load=canonical["normalized_offered_load"],  # type: ignore[arg-type]
        )
    except (KeyError, TypeError, ValueError):
        raise _fail("canonical_baseline_projection_invalid") from None
    if baseline.get("payload_sha256") != canonical.get("baseline_plan_payload_sha256"):
        raise _fail("canonical_baseline_binding_invalid")
    digest = hashlib.sha256(
        (
            f"{full_history_run_id}\0{namespace}\0{frozen_method_sha256}"
        ).encode("ascii")
    ).hexdigest()
    try:
        return build_membind_v31_live_plan(
            run_id=f"membind-v31-v4-{digest[:24]}",
            verified_baseline_plan=baseline,
            methodology_sha256=canonical["methodology_sha256"],  # type: ignore[arg-type]
            workplan_sha256=canonical["workplan_sha256"],  # type: ignore[arg-type]
        )
    except (KeyError, TypeError, ValueError):
        raise _fail("fresh_v31_plan_invalid") from None


def build_v4_full_history_runner(
    *,
    paths: ProductionExecutorPaths | None = None,
    loaders: V4ProductionLoaders | None = None,
    base_hooks_factory: Callable[[], V31LiveHooks] | None = None,
    factorized_adapter_factory: Callable[[object, StateCutCertification], object] | None = None,
    execute_block: Callable[..., object] = execute_v4_live_block,
) -> Callable[..., Mapping[str, object]]:
    """Build the production callback consumed by ``run_v4_full``.

    Each invocation derives a new verified v3.1 plan identity and executes its
    matching MemBind block in the full-run namespace.  The inner block lives
    below ``history_root/block`` so the outer full-run orchestrator exclusively
    owns ``history_root/result.json`` and its resume protocol.
    """

    selected_paths = ProductionExecutorPaths.from_repository(Path(__file__).resolve().parents[4]) if paths is None else paths
    if not isinstance(selected_paths, ProductionExecutorPaths):
        raise _fail("production_paths_invalid")
    selected_loaders = production_v4_loaders(selected_paths) if loaders is None else loaders
    if not isinstance(selected_loaders, V4ProductionLoaders):
        raise _fail("production_loaders_invalid")
    if not callable(execute_block):
        raise _fail("execute_block_invalid")
    try:
        canonical = verify_membind_v31_method_plan(
            selected_loaders.load_plan(selected_paths.control_root)
        )
    except ValueError:
        raise _fail("canonical_plan_invalid") from None
    loaded: dict[str, object] = {}

    def context() -> tuple[Mapping[str, str], StateCutCertification, Mapping[str, Sequence[object]]]:
        if not loaded:
            env = selected_loaders.load_env(selected_paths.env_file)
            certification = selected_loaders.load_certification(selected_paths.freeze_paths)
            episodes = selected_loaders.load_episodes(selected_paths.development_input, canonical)
            if (
                not isinstance(env, Mapping)
                or any(not isinstance(key, str) or not isinstance(value, str) for key, value in env.items())
                or not isinstance(certification, StateCutCertification)
                or tuple(episodes) != tuple(canonical["histories"])
            ):
                raise _fail("production_context_invalid")
            loaded.update(env=dict(env), certification=certification, episodes=episodes)
        return loaded["env"], loaded["certification"], loaded["episodes"]  # type: ignore[return-value]

    def run_history(**kwargs: object) -> Mapping[str, object]:
        history_index = kwargs.get("history_index")
        history_id = kwargs.get("history_id")
        full_run_id = kwargs.get("run_id")
        namespace = kwargs.get("namespace")
        source_count = kwargs.get("source_count")
        history_root = kwargs.get("history_root")
        frozen_method = kwargs.get("frozen_method")
        frozen_method_path = kwargs.get("frozen_method_path")
        preflight = kwargs.get("preflight")
        if (
            isinstance(history_index, bool)
            or not isinstance(history_index, int)
            or not 0 <= history_index < len(FORMAL_HISTORY_IDS)
            or history_id != FORMAL_HISTORY_IDS[history_index]
        ):
            raise _fail("full_history_identity_invalid")
        if (
            not isinstance(full_run_id, str)
            or not full_run_id
            or not isinstance(namespace, str)
            or not namespace
            or not isinstance(history_root, Path)
            or kwargs.get("fresh_namespace") is not True
            or kwargs.get("runner_mode") != "live"
        ):
            raise _fail("full_history_runtime_identity_invalid")
        if (
            not isinstance(source_count, int)
            or isinstance(source_count, bool)
            or source_count != len(canonical["history_source_sha256s"][history_id])
        ):
            raise _fail("full_history_source_count_invalid")
        if not isinstance(preflight, Mapping) or preflight.get("status") != "READY" or preflight.get("classification") != "READY":
            raise _fail("full_history_preflight_not_ready")
        if not isinstance(frozen_method, Mapping) or not isinstance(frozen_method_path, Path):
            raise _fail("frozen_method_invalid")
        verified_frozen = verify_frozen_method(frozen_method_path)
        if dict(frozen_method) != verified_frozen:
            raise _fail("frozen_method_binding_invalid")
        plan = _fresh_v31_plan(
            canonical,
            full_history_run_id=full_run_id,
            namespace=namespace,
            frozen_method_sha256=str(verified_frozen["payload_sha256"]),
        )
        block_indices = [
            index
            for index, block in enumerate(plan["blocks"])
            if block["method"] == "MemBind" and block["history_id"] == history_id
        ]
        if len(block_indices) != 1:
            raise _fail("fresh_v31_history_block_invalid")
        env, certification, episodes = context()
        block_root = history_root / "block"
        hooks = base_hooks_factory() if base_hooks_factory is not None else None
        produced = execute_block(
            verified_plan=plan,
            block_index=block_indices[0],
            episodes=episodes[history_id],
            env=env,
            block_root=block_root,
            state_cut_certification=certification,
            compile_workers=int(plan["compile_workers"]),
            lookahead=int(plan["lookahead"]),
            stream_id=history_id,
            namespace_override=namespace,
            base_hooks=hooks,
            factorized_adapter_factory=factorized_adapter_factory,
        )
        result = asyncio.run(produced) if inspect.isawaitable(produced) else produced
        if not isinstance(result, Mapping):
            raise _fail("full_history_block_result_invalid")
        telemetry = result.get("telemetry")
        performance = result.get("performance")
        per_source = performance.get("per_source") if isinstance(performance, Mapping) else None
        if isinstance(per_source, Sequence) and not isinstance(per_source, (str, bytes)):
            if len(per_source) != source_count:
                raise _fail("full_history_performance_coverage_invalid")
            for sequence, row in enumerate(per_source):
                if not isinstance(row, Mapping) or row.get("source_sequence") != sequence:
                    raise _fail("full_history_performance_coverage_invalid")
        if (
            result.get("status") != "PASS"
            or result.get("run_id") != plan["run_id"]
            or result.get("history_id") != history_id
            or result.get("namespace") != namespace
            or result.get("source_count") != source_count
            or result.get("direct_violation_count") != 0
            or not isinstance(performance, Mapping)
            or isinstance(per_source, (str, bytes))
            or not isinstance(per_source, Sequence)
            or not isinstance(telemetry, Mapping)
            or telemetry.get("persistent_write_count") != 0
        ):
            raise _fail("full_history_block_result_invalid")
        return {
            "status": "PASS",
            "history_id": history_id,
            "run_id": full_run_id,
            "namespace": namespace,
            "source_count": source_count,
            "direct_violation_count": 0,
            "performance": deepcopy(result.get("performance")),
            "telemetry": deepcopy(telemetry),
            "admission_observation": deepcopy(result.get("admission_observation")),
            "output_artifacts": {
                "block_root": str(block_root.resolve()),
                "fresh_v31_plan_payload_sha256": plan["payload_sha256"],
                "fresh_v31_plan_run_id": plan["run_id"],
                "v4_block_result_payload_sha256": result.get("payload_sha256"),
            },
        }

    return run_history


def build_v4_full_run_history_runner(**kwargs: object) -> Callable[..., Mapping[str, object]]:
    """Compatibility alias for the formal runner factory."""

    return build_v4_full_history_runner(**kwargs)  # type: ignore[arg-type]


__all__ = [
    "V4LiveBlockError",
    "V4LiveBlockComposition",
    "V4ProductionLoaders",
    "build_v4_live_composition",
    "build_v4_live_hooks",
    "build_v4_live_block_hooks",
    "production_v4_live_hooks",
    "execute_v4_live_block",
    "production_v4_loaders",
    "build_v4_production_block_runner",
    "build_v4_full_history_runner",
    "build_v4_full_run_history_runner",
]
