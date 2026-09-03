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
