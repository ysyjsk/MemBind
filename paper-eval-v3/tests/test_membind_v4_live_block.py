"""TDD contracts for the v4 production block composition."""

from __future__ import annotations

import asyncio
import json
import importlib.util
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from paper_eval.membind_v31.live_block import V31LiveHooks
from paper_eval.membind_v31.certification import StateCutCertification
from paper_eval.membind_v31.production_executor import ProductionExecutorPaths
from paper_eval.artifacts import atomic_write_json, payload_sha256
from paper_eval.membind_v4.live_adapter import V4LiveNodeResolveError
from paper_eval.membind_v4.speculative_adapter import V4SpeculativeGraphitiAdapter
from paper_eval.membind_v4.live_block import (
    V4ProductionLoaders,
    V4LiveBlockError,
    _build_production_identity_metadata,
    _build_graphiti_semantic_encoder,
    build_v4_full_history_runner,
    build_v4_live_hooks,
)


class _NativeWithoutFactorization:
    async def prepare(self, _input):
        raise AssertionError("prepare is not reached in the fail-closed test")

    async def bind(self, *_args, **_kwargs):
        raise AssertionError("bind is not reached in the fail-closed test")


class _NativeWithFactorization(_NativeWithoutFactorization):
    def v4_node_resolve_callbacks(self):
        async def materialize(_input, prepared, state_version):
            raise AssertionError("callbacks are not reached in the composition test")

        return {
            "materialize_request": materialize,
            "execute_request": lambda request: request,
            "interpret_response": lambda response, _call: response,
            "continue_native_bind": lambda *_args, **_kwargs: None,
        }


def _base_hooks(calls: list[dict[str, object]]) -> V31LiveHooks:
    def runtime_builder(**kwargs):
        calls.append(dict(kwargs))
        return SimpleNamespace(shared_execution_envelope_sha256="e" * 64)

    async def close_runtime(_runtime):
        calls.append({"closed": True})

    return V31LiveHooks(
        runtime_builder=runtime_builder,
        runtime_ready=lambda _runtime: asyncio.sleep(0),
        namespace_probe=lambda *_args: asyncio.sleep(0, result={}),
        namespace_episode=lambda episode, _namespace: episode,
        source_visibility_probe=lambda *_args: asyncio.sleep(0, result=True),
        reference_time_to_ns=lambda _value: 0,
        adapter_factory=lambda *_args: _NativeWithFactorization(),
        close_runtime=close_runtime,
    )


def test_v4_hooks_inject_admission_observer_and_stream_identity() -> None:
    calls: list[dict[str, object]] = []
    hooks = build_v4_live_hooks(
        stream_id="history-a",
        base_hooks=_base_hooks(calls),
        factorized_adapter_factory=lambda *_args: _NativeWithFactorization(),
    )

    runtime = hooks.runtime_builder(env={}, policy=object(), request_id_prefix="v4")
    assert calls and callable(calls[0]["admission_observer"])
    observer = calls[0]["admission_observer"]
    observer(
        {
            "configured_limit": 2,
            "active_count": 1,
            "active_frontier_count": 1,
            "waiting_frontier_count": 0,
            "frontier_bind_region_count": 1,
            "frontier_transport_phase": "FRONTIER_LLM_PERMIT_ACTIVE",
        }
    )
    adapter = hooks.adapter_factory(runtime, SimpleNamespace())
    assert isinstance(adapter, V4SpeculativeGraphitiAdapter)
    assert adapter.telemetry()["active_speculation_count"] == 0

    asyncio.run(hooks.close_runtime(runtime))
    assert calls[-1] == {"closed": True}


def test_v4_hooks_fail_closed_when_graphiti_factorization_is_unavailable() -> None:
    hooks = build_v4_live_hooks(
        stream_id="history-a",
        base_hooks=_base_hooks([]),
        factorized_adapter_factory=lambda *_args: _NativeWithoutFactorization(),
    )
    runtime = hooks.runtime_builder(env={}, policy=object(), request_id_prefix="v4")
    with pytest.raises(V4LiveNodeResolveError, match="node_resolve_factorization_unavailable"):
        hooks.adapter_factory(runtime, SimpleNamespace())


def test_production_semantic_encoder_projects_graphiti_call_before_transport() -> None:
    class Tokenizer:
        def apply_chat_template(self, messages, **_kwargs):
            return [ord(character) for row in messages for character in row["content"]]

    class RawLLM:
        structured_output_mode = "json_schema"

        @staticmethod
        def _apply_attribute_extraction_preamble(_messages, _enabled):
            return None

        @staticmethod
        def _clean_input(value):
            return value

    class TransportEncoder:
        _tokenizer = Tokenizer()
        _unit = 2
        _identity = "a" * 64
        _cache_identity = "b" * 64
        _trace_hmac_key = b"k" * 32

        def __call__(self, *_args, **_kwargs):
            raise AssertionError("transport encoder cannot accept Graphiti arguments")

    runtime = SimpleNamespace(
        raw_llm=RawLLM(),
        admitted_llm=SimpleNamespace(_prefix_encoder=TransportEncoder()),
    )
    encode = _build_graphiti_semantic_encoder(
        runtime,
        multilingual_instruction=lambda group_id: f"|lang:{group_id}",
    )
    captured = SimpleNamespace(
        args=([{"role": "system", "content": "system"}, {"role": "user", "content": "user"}],),
        kwargs={"group_id": "ns-a", "prompt_name": "dedupe_nodes.nodes"},
    )
    first = encode(captured)
    assert first["prompt_tokens"] == len("system|lang:ns-auser")
    assert len(first["rendered_request_sha256"]) == 64
    assert len(first["token_sequence_sha256"]) == 64

    changed = encode(
        SimpleNamespace(
            args=([{"role": "system", "content": "system"}, {"role": "user", "content": "changed"}],),
            kwargs={"group_id": "ns-a", "prompt_name": "dedupe_nodes.nodes"},
        )
    )
    assert changed["token_sequence_sha256"] != first["token_sequence_sha256"]


def test_production_identity_binds_actual_client_and_prefix_configuration() -> None:
    construction = {
        "base_url": "http://10.87.5.247:8000/v1",
        "served_model_id": "qwen3-32b-fp8",
        "requested_max_tokens": 16_384,
    }
    prefix_identity = {
        "schema_version": "membind.paper-eval-v3.qwen-prefix-encoder.v1",
        "tokenizer_identity_sha256": "a" * 64,
        "trace_key_identity_sha256": "b" * 64,
        "cache_identity_sha256": "c" * 64,
        "prefix_match_unit": 16,
    }
    runtime = SimpleNamespace(
        shared_public_identity={"construction": construction},
        admitted_llm=SimpleNamespace(
            _prefix_encoder=SimpleNamespace(public_identity=prefix_identity)
        ),
    )
    llm = SimpleNamespace(
        model="qwen3-32b-fp8",
        max_tokens=16_384,
        temperature=0.0,
        structured_output_mode="json_schema",
        config=SimpleNamespace(
            model="qwen3-32b-fp8",
            base_url="http://10.87.5.247:8000/v1",
            temperature=0.0,
        ),
        client=SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(_structured_backend_identity="xgrammar")
            )
        ),
    )

    identity = _build_production_identity_metadata(
        runtime=runtime,
        llm=llm,
        semantic_binding_identity_sha256="d" * 64,
        response_schema={"type": "object"},
    )

    assert identity["model_identity"] == construction
    assert identity["decoding_identity"]["temperature"] == 0.0
    assert identity["decoding_identity"]["structured_backend_identity"] == "xgrammar"
    assert identity["operator_identity"]["prefix_encoder_identity"] == prefix_identity


def test_production_identity_rejects_decoding_or_prefix_drift() -> None:
    construction = {
        "base_url": "http://10.87.5.247:8000/v1",
        "served_model_id": "qwen3-32b-fp8",
        "requested_max_tokens": 16_384,
    }
    prefix_identity = {
        "schema_version": "membind.paper-eval-v3.qwen-prefix-encoder.v1",
        "tokenizer_identity_sha256": "a" * 64,
        "trace_key_identity_sha256": "b" * 64,
        "cache_identity_sha256": "c" * 64,
        "prefix_match_unit": 16,
    }
    runtime = SimpleNamespace(
        shared_public_identity={"construction": construction},
        admitted_llm=SimpleNamespace(
            _prefix_encoder=SimpleNamespace(public_identity=prefix_identity)
        ),
    )
    llm = SimpleNamespace(
        model="qwen3-32b-fp8",
        max_tokens=16_384,
        temperature=0.1,
        structured_output_mode="json_schema",
        config=SimpleNamespace(base_url="http://10.87.5.247:8000/v1"),
        client=SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(_structured_backend_identity="wrong")
            )
        ),
    )
    with pytest.raises(V4LiveBlockError, match="factorized_llm_identity_mismatch"):
        _build_production_identity_metadata(
            runtime=runtime,
            llm=llm,
            semantic_binding_identity_sha256="d" * 64,
            response_schema={"type": "object"},
        )

    runtime.admitted_llm._prefix_encoder.public_identity = {"schema_version": "broken"}
    llm.temperature = 0.0
    llm.client.chat.completions._structured_backend_identity = "xgrammar"
    with pytest.raises(V4LiveBlockError, match="factorized_prefix_identity_invalid"):
        _build_production_identity_metadata(
            runtime=runtime,
            llm=llm,
            semantic_binding_identity_sha256="d" * 64,
            response_schema={"type": "object"},
        )


def test_default_production_composition_uses_factorized_factory(monkeypatch) -> None:
    calls: list[dict[str, object]] = []
    base = _base_hooks(calls)

    import paper_eval.membind_v4.live_block as module

    monkeypatch.setattr(module, "production_v31_live_hooks", lambda: base)

    def factorized(_runtime, _certification):
        calls.append({"factorized": True})
        return _NativeWithFactorization()

    monkeypatch.setattr(module, "_production_factorized_adapter_factory", factorized)
    composition = module.build_v4_live_composition(stream_id="history-a")
    runtime = composition.hooks.runtime_builder(
        env={}, policy=object(), request_id_prefix="v4"
    )
    adapter = composition.hooks.adapter_factory(runtime, SimpleNamespace())
    assert isinstance(adapter, V4SpeculativeGraphitiAdapter)
    assert {"factorized": True} in calls


def test_full_history_runner_builds_fresh_plan_and_binds_full_run_identity(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[1]
    plan = json.loads(
        (project / "artifacts/paper_eval/membind_v31/V31_METHOD_PLAN.json").read_text(
            encoding="utf-8"
        )
    )
    certification = object.__new__(StateCutCertification)
    episodes = {
        history: tuple(range(len(sources)))
        for history, sources in plan["history_source_sha256s"].items()
    }
    episodes = {history: episodes[history] for history in plan["histories"]}
    paths = ProductionExecutorPaths.from_repository(tmp_path)
    loaders = V4ProductionLoaders(
        load_plan=lambda _path: plan,
        load_env=lambda _path: {"SAFE": "value"},
        load_certification=lambda _paths: certification,
        load_episodes=lambda _path, _plan: episodes,
    )
    calls: list[dict[str, object]] = []

    async def execute_block(**kwargs):
        calls.append(dict(kwargs))
        selected = kwargs["verified_plan"]
        block = selected["blocks"][kwargs["block_index"]]
        return {
            "status": "PASS",
            "run_id": selected["run_id"],
            "history_id": block["history_id"],
            "namespace": kwargs["namespace_override"],
            "source_count": len(kwargs["episodes"]),
            "direct_violation_count": 0,
            "performance": {
                "makespan_ns": 1,
                "per_source": [
                    {"source_sequence": index, "freshness_ns": index + 1}
                    for index in range(len(kwargs["episodes"]))
                ],
            },
            "telemetry": {"persistent_write_count": 0},
            "admission_observation": {"configured_limit": 2},
            "payload_sha256": "a" * 64,
        }

    frozen = {
        "schema_version": "membind.paper-eval-v4.frozen-method.v1",
        "status": "FROZEN",
        "candidate_id": "c01",
        "policy": "IDLE_SLOT_VALIDATED_SPEC",
        "thresholds": {"global_k": 2, "speculation_distance": 1},
        "formal_history_ids": ["07741c45", "6071bd76", "a2f3aa27", "b6019101"],
    }
    frozen["payload_sha256"] = payload_sha256(frozen)
    frozen_path = tmp_path / "V4_FROZEN_METHOD.json"
    atomic_write_json(frozen_path, frozen)
    runner = build_v4_full_history_runner(
        paths=paths,
        loaders=loaders,
        execute_block=execute_block,
    )
    result = runner(
        history_index=1,
        history_id="6071bd76",
        run_id="v4-full-live-h01-6071bd76",
        namespace="membind-v4-fresh-6071bd76",
        source_count=46,
        fresh_namespace=True,
        history_root=tmp_path / "history",
        frozen_method=frozen,
        frozen_method_path=frozen_path,
        preflight={"status": "READY", "classification": "READY"},
        runner_mode="live",
    )

    assert result["run_id"] == "v4-full-live-h01-6071bd76"
    assert result["namespace"] == "membind-v4-fresh-6071bd76"
    assert result["source_count"] == 46
    assert calls[0]["namespace_override"] == "membind-v4-fresh-6071bd76"
    assert calls[0]["block_root"] == tmp_path / "history" / "block"
    generated = calls[0]["verified_plan"]
    assert generated["run_id"].startswith("membind-v31-v4-")
    assert generated["run_id"] != plan["run_id"]


def test_v4_block_projects_per_source_freshness_from_durable_events(tmp_path: Path) -> None:
    """The public v4 block projection must cover every published source."""

    from paper_eval.membind_v4.live_block import _per_source_freshness

    events = [
        {"event_type": "ARRIVAL", "source_sequence": 0, "timestamp_ns": 100},
        {"event_type": "ARRIVAL", "source_sequence": 1, "timestamp_ns": 200},
        {"event_type": "PUBLICATION_DURABLE", "source_sequence": 0, "timestamp_ns": 130},
        {"event_type": "PUBLICATION_DURABLE", "source_sequence": 1, "timestamp_ns": 260},
    ]
    assert _per_source_freshness(events) == {
        "freshness_ns": [30, 60],
        "per_source": [
            {
                "source_sequence": 0,
                "arrival_timestamp_ns": 100,
                "publication_timestamp_ns": 130,
                "freshness_ns": 30,
            },
            {
                "source_sequence": 1,
                "arrival_timestamp_ns": 200,
                "publication_timestamp_ns": 260,
                "freshness_ns": 60,
            },
        ],
    }


def test_v4_live_metrics_are_recomputed_from_lifecycle_and_sealed_llm_trace(
    tmp_path: Path,
) -> None:
    """MISS tokens are waste; validated HIT and native tokens remain useful."""

    from paper_eval.membind_v4.live_block import _derive_live_metrics

    events = [
        {"event_type": "ARRIVAL", "source_sequence": 0, "timestamp_ns": 0},
        {"event_type": "BIND_STARTED", "source_sequence": 0, "timestamp_ns": 10},
        {
            "event_type": "PUBLICATION_DURABLE",
            "source_sequence": 0,
            "timestamp_ns": 50,
        },
        {"event_type": "ARRIVAL", "source_sequence": 1, "timestamp_ns": 60},
        {"event_type": "BIND_STARTED", "source_sequence": 1, "timestamp_ns": 70},
        {
            "event_type": "PUBLICATION_DURABLE",
            "source_sequence": 1,
            "timestamp_ns": 160,
        },
    ]
    llm_rows = [
        {
            "event_type": "llm_request_submitted",
            "request_id": "v4:00000000",
            "source_sequence": 0,
            "timestamp_ns": 20,
            "token_sequence_hmac_sha256": "a" * 64,
        },
        {
            "event_type": "llm_transport_response",
            "transport_attempt_index": 0,
            "source_sequence": 0,
            "prompt_tokens": 80,
            "completion_tokens": 20,
            "total_tokens": 100,
        },
        {
            "event_type": "llm_request_submitted",
            "request_id": "v4:00000001",
            "source_sequence": 1,
            "timestamp_ns": 80,
            "token_sequence_hmac_sha256": "b" * 64,
        },
        {
            "event_type": "llm_transport_response",
            "transport_attempt_index": 1,
            "source_sequence": 1,
            "prompt_tokens": 40,
            "completion_tokens": 10,
            "total_tokens": 50,
        },
    ]
    trace = tmp_path / "llm.jsonl"
    with trace.open("w", encoding="utf-8") as handle:
        for row in llm_rows:
            record = {
                "schema_version": "membind.paper-eval-v3.membind-v31-llm.v1",
                "row": row,
            }
            wrapper = {"record": record, "record_sha256": payload_sha256(record)}
            handle.write(json.dumps(wrapper, sort_keys=True) + "\n")
    telemetry = {
        "events": [
            {
                "event_type": "semantic_miss",
                "source_sequence": 1,
                "execution_mode": "LLM",
                "token_sequence_hmac_sha256": "b" * 64,
                "speculation_started_timestamp_ns": 75,
                "speculation_completed_timestamp_ns": 140,
            }
        ]
    }

    metrics = _derive_live_metrics(
        events=events,
        telemetry=telemetry,
        llm_path=trace,
    )

    assert metrics["frontier_service_ns"] == [40, 90]
    assert metrics["frontier_p95_service_ns"] == pytest.approx(87.5)
    assert metrics["llm_successful_token_count"] == 150
    assert metrics["miss_speculative_token_count"] == 50
    assert metrics["useful_token_count"] == 100
    assert metrics["makespan_ns"] == 160
    assert metrics["useful_token_throughput_tokens_per_second"] == pytest.approx(
        625_000_000
    )


def test_v4_live_metrics_fail_closed_when_miss_hmac_is_not_in_trace(
    tmp_path: Path,
) -> None:
    from paper_eval.membind_v4.live_block import V4LiveBlockError, _derive_live_metrics

    trace = tmp_path / "llm.jsonl"
    trace.write_text("", encoding="utf-8")
    with pytest.raises(V4LiveBlockError, match="speculative_llm_trace_alignment_failed"):
        _derive_live_metrics(
            events=[
                {"event_type": "ARRIVAL", "source_sequence": 0, "timestamp_ns": 0},
                {"event_type": "BIND_STARTED", "source_sequence": 0, "timestamp_ns": 1},
                {
                    "event_type": "PUBLICATION_DURABLE",
                    "source_sequence": 0,
                    "timestamp_ns": 2,
                },
            ],
            telemetry={
                "events": [
                    {
                        "event_type": "semantic_miss",
                        "source_sequence": 0,
                        "execution_mode": "LLM",
                        "token_sequence_hmac_sha256": "c" * 64,
                        "speculation_started_timestamp_ns": 0,
                        "speculation_completed_timestamp_ns": 2,
                    }
                ]
            },
            llm_path=trace,
        )


def test_v4_live_metrics_align_miss_using_speculative_hmac(
    tmp_path: Path,
) -> None:
    """A MISS must charge the speculative request even when exact differs."""

    from paper_eval.membind_v4.live_block import _derive_live_metrics

    trace = tmp_path / "llm.jsonl"
    rows = [
        {
            "event_type": "llm_request_submitted",
            "request_id": "v4:00000000",
            "source_sequence": 0,
            "timestamp_ns": 10,
            "token_sequence_hmac_sha256": "s" * 64,
        },
        {
            "event_type": "llm_transport_response",
            "transport_attempt_index": 0,
            "source_sequence": 0,
            "total_tokens": 7,
        },
    ]
    with trace.open("w", encoding="utf-8") as handle:
        for row in rows:
            record = {
                "schema_version": "membind.paper-eval-v3.membind-v31-llm.v1",
                "row": row,
            }
            handle.write(
                json.dumps(
                    {"record": record, "record_sha256": payload_sha256(record)},
                    sort_keys=True,
                )
                + "\n"
            )

    metrics = _derive_live_metrics(
        events=[
            {"event_type": "ARRIVAL", "source_sequence": 0, "timestamp_ns": 0},
            {"event_type": "BIND_STARTED", "source_sequence": 0, "timestamp_ns": 1},
            {"event_type": "PUBLICATION_DURABLE", "source_sequence": 0, "timestamp_ns": 20},
        ],
        telemetry={
            "events": [
                {
                    "event_type": "semantic_miss",
                    "source_sequence": 0,
                    "execution_mode": "LLM",
                    "token_sequence_hmac_sha256": "e" * 64,
                    "speculative_token_sequence_hmac_sha256": "s" * 64,
                    "speculation_started_timestamp_ns": 5,
                    "speculation_completed_timestamp_ns": 15,
                }
            ]
        },
        llm_path=trace,
    )

    assert metrics["miss_speculative_token_count"] == 7
    assert metrics["useful_token_count"] == 0


def test_full_cli_default_history_runner_is_lazy_and_callable() -> None:
    script_path = Path(__file__).resolve().parents[1] / "scripts/run_membind_v4_full.py"
    spec = importlib.util.spec_from_file_location("membind_v4_full_cli_test", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls: list[dict[str, object]] = []

    def fake_builder():
        calls.append({"built": True})

        def runner(**kwargs):
            return {"status": "PASS", **kwargs}

        return runner

    original = module.build_v4_full_history_runner
    module.build_v4_full_history_runner = fake_builder
    try:
        runner = module._load_runner(None)
        assert callable(runner)
        assert calls == []
        assert runner(history_id="07741c45")["status"] == "PASS"
        assert calls == [{"built": True}]
        runner(history_id="b6019101")
        assert calls == [{"built": True}]
    finally:
        module.build_v4_full_history_runner = original


def test_full_cli_live_blocked_preflight_uses_default_runner_path_without_services(tmp_path: Path) -> None:
    frozen = {
        "schema_version": "membind.paper-eval-v4.frozen-method.v1",
        "status": "FROZEN",
        "candidate_id": "c01",
        "policy": "IDLE_SLOT_VALIDATED_SPEC",
        "thresholds": {"global_k": 2, "speculation_distance": 1},
        "formal_history_ids": ["07741c45", "6071bd76", "a2f3aa27", "b6019101"],
    }
    frozen["payload_sha256"] = payload_sha256(frozen)
    frozen_path = tmp_path / "V4_FROZEN_METHOD.json"
    atomic_write_json(frozen_path, frozen)
    preflight = {
        "status": "BLOCKED_SERVICE_PREFLIGHT",
        "classification": "EXECUTION_SANDBOX_NETWORK_ISOLATION",
    }
    preflight["payload_sha256"] = payload_sha256(preflight)
    preflight_path = tmp_path / "PREFLIGHT.json"
    atomic_write_json(preflight_path, preflight)
    script = Path(__file__).resolve().parents[1] / "scripts/run_membind_v4_full.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--frozen-method",
            str(frozen_path),
            "--fresh-namespaces",
            "--mode",
            "live",
            "--preflight",
            str(preflight_path),
            "--run-id",
            "v4-full-default-runner-test",
            "--output-root",
            str(tmp_path / "run"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1
    public = json.loads(completed.stdout)
    assert public["status"] == "FAILED_NON_MERGEABLE"
    assert public["classification"] == "EXECUTION_SANDBOX_NETWORK_ISOLATION"
