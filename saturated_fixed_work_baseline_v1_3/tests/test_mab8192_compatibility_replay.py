from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _module():
    path = ROOT / "scripts/run_mab8192_compatibility_replay.py"
    spec = importlib.util.spec_from_file_location("mab8192_compatibility_replay", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_wire_request_uses_actual_message_hash_and_official_p0_sampling() -> None:
    module = _module()
    module.DEPLOYMENT_POLICY = module.resolve_deployment_policy(
        {
            "MEMBIND_DEPLOYMENT_POLICY_ID": "P0_QWEN3_8B_AWQ",
            "MEMBIND_PROFILE_ID": "local-qwen3-8b-awq-dualreplica-v1",
            "MEMBIND_LLM_MODEL_NAME": "qwen3-8b-awq",
            "MEMBIND_LLM_MODEL_REVISION": "4da05a8edb55c6046cce958586c33b61da07bb79",
        }
    )
    request = {
        "model": "qwen3-8b-awq",
        "messages": [
            {"role": "system", "content": "extract"},
            {"role": "user", "content": "payload"},
        ],
        "temperature": 0,
        "max_tokens": 16384,
    }
    wire, identity = module._wire_request(
        request,
        context_id="ctx",
        source_sequence=4,
        chunk_ordinal=2,
        prompt_name="extract_edges.edge",
    )
    assert wire["messages"] is request["messages"]
    assert identity["canonical_messages_hash"] == module.request_hash(
        {"messages": request["messages"]}
    )
    assert wire["temperature"] == 0.7
    assert wire["top_p"] == 0.8
    assert wire["presence_penalty"] == 1.5
    assert wire["extra_body"] == {
        "top_k": 20,
        "min_p": 0,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    assert wire["seed"] == module.logical_request_seed(identity)


def test_wire_request_p1_is_independent_and_excludes_p0_only_fields() -> None:
    module = _module()
    module.DEPLOYMENT_POLICY = module.resolve_deployment_policy(
        {
            "MEMBIND_DEPLOYMENT_POLICY_ID": "P1_QWEN25_7B_AWQ",
            "MEMBIND_PROFILE_ID": "local-qwen25-7b-awq-dualreplica-v1",
            "MEMBIND_LLM_MODEL_NAME": "qwen2.5-7b-instruct-awq",
            "MEMBIND_LLM_MODEL_REVISION": "b25037543e9394b818fdfca67ab2a00ecc7dd641",
        }
    )
    wire, _identity = module._wire_request(
        {
            "model": "qwen2.5-7b-instruct-awq",
            "messages": [{"role": "user", "content": "payload"}],
            "max_tokens": 16384,
        },
        context_id="ctx",
        source_sequence=4,
        chunk_ordinal=2,
        prompt_name="extract_edges.edge",
    )
    assert wire["temperature"] == 0.7
    assert wire["top_p"] == 0.8
    assert wire["extra_body"] == {"top_k": 20, "repetition_penalty": 1.05}
    assert "presence_penalty" not in wire
    assert "min_p" not in wire["extra_body"]
    assert "chat_template_kwargs" not in wire["extra_body"]
