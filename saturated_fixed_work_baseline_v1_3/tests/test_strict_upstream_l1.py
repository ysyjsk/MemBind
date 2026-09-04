from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "saturated_fixed_work_baseline_v1_3"
    / "scripts"
    / "run_strict_upstream_l1.py"
)


def _module():
    scripts = str(SCRIPT.parent)
    sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location("run_strict_upstream_l1", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(scripts)
    return module


def test_l1_selects_the_preserved_real_growing_history_failure() -> None:
    module = _module()
    target, manifest = module._load_target_episode()

    assert manifest.manifest_sha256 == module.EXPECTED_MANIFEST_SHA256
    assert target.source_sequence == 13
    assert target.original_source_sequence == 3
    assert target.chunk_ordinal == 2
    assert target.chunk_id == "chunk-629c22ceea0b38f1137fb847aa36c4c7"


@pytest.mark.asyncio
async def test_l1_interceptor_aborts_only_on_exact_target_wire_messages() -> None:
    module = _module()
    calls = []

    class Completions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(ok=True)

    captured = {}
    target_messages = [{"role": "user", "content": "exact target"}]
    target_messages_sha256 = module._wire_messages_sha256(target_messages)
    interceptor = module._CaptureCompletions(
        Completions(),
        endpoint_id="native-replica",
        target_messages_sha256=target_messages_sha256,
        capture=captured,
    )
    ordinary = [{"role": "user", "content": "ordinary"}]
    assert (await interceptor.create(messages=ordinary)).ok is True

    with pytest.raises(module.TargetRequestCaptured):
        await interceptor.create(
            model="qwen2.5-7b-instruct-awq",
            messages=target_messages,
            max_tokens=16384,
            response_format={"type": "json_schema"},
        )

    assert len(calls) == 1
    assert captured["endpoint_id"] == "native-replica"
    assert captured["wire_messages_sha256"] == target_messages_sha256


def test_l1_response_gate_requires_stop_and_schema_validity() -> None:
    module = _module()
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content='{"edges": []}'),
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=12979,
            completion_tokens=8,
            total_tokens=12987,
        ),
    )

    result = module._evaluate_target_response(response)

    assert result["status"] == "PASS"
    assert result["json_valid"] is True
    assert result["pydantic_valid"] is True
    assert result["schema_valid"] is True
    assert result["reached_token_limit"] is False
    assert result["response_repair_enabled"] is False


def test_l1_response_gate_rejects_length_stop() -> None:
    module = _module()
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="length",
                message=SimpleNamespace(content='{"edges": []}'),
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=12979,
            completion_tokens=16384,
            total_tokens=29363,
        ),
    )

    result = module._evaluate_target_response(response)

    assert result["status"] == "FAIL"
    assert result["reached_token_limit"] is True


def test_p2_l1_expected_request_changes_only_declared_deployment_fields() -> None:
    module = _module()
    historical = {
        "model": "qwen2.5-7b-instruct-awq",
        "messages": [{"role": "user", "content": "same upstream prompt"}],
        "max_tokens": 16384,
        "temperature": 0.7,
        "top_p": 0.8,
        "seed": 3248099774,
        "response_format": {"type": "json_schema"},
        "extra_body": {"top_k": 20, "repetition_penalty": 1.05},
    }

    expected, changed_paths = module._expected_candidate_wire_request(
        historical,
        module.P2_DEPLOYMENT_POLICY,
    )

    assert expected == {
        **historical,
        "model": "qwen3-14b-awq",
        "temperature": 0.6,
        "top_p": 0.95,
        "extra_body": {
            "top_k": 20,
            "chat_template_kwargs": {"enable_thinking": False},
        },
    }
    assert changed_paths == [
        "extra_body.chat_template_kwargs",
        "extra_body.repetition_penalty",
        "model",
        "temperature",
        "top_p",
    ]


def test_p2_l1_does_not_regenerate_the_authenticated_prompt() -> None:
    module = _module()
    source = inspect.getsource(module.run)

    assert ".add_episode(" not in source
    assert "_install_target_capture" not in source
    assert "RECONSTRUCTING_EXACT_REQUEST" not in source
    assert "SUBMITTING_AUTHENTICATED_CAPTURE" in source
