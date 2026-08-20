from __future__ import annotations

import json
from pathlib import Path

import pytest

from paper_eval.membind_v4.siliconflow_probe import (
    SILICONFLOW_BASE_URL,
    SILICONFLOW_CHAT_MODEL,
    SILICONFLOW_EMBEDDING_MODEL,
    SiliconFlowProbeError,
    normalize_siliconflow_chat_request,
    run_siliconflow_probe,
)


def _fake_transport_factory(*, chat_content: str = '{"status":"ok","count":1}',
                            chat_model: str = SILICONFLOW_CHAT_MODEL,
                            embedding_dimension: int = 1024):
    calls: list[tuple[str, str, dict[str, str], dict[str, object]]] = []

    def transport(
        method: str,
        url: str,
        headers: dict[str, str],
        body: dict[str, object] | None,
        timeout: float,
    ) -> tuple[int, bytes]:
        assert timeout > 0
        calls.append((method, url, dict(headers), dict(body or {})))
        if url.endswith("/models"):
            return 200, json.dumps(
                {
                    "data": [
                        {"id": SILICONFLOW_CHAT_MODEL},
                        {"id": SILICONFLOW_EMBEDDING_MODEL},
                    ]
                }
            ).encode()
        if url.endswith("/embeddings"):
            return 200, json.dumps(
                {
                    "model": SILICONFLOW_EMBEDDING_MODEL,
                    "data": [{"embedding": [0.0] * embedding_dimension}],
                    "usage": {"prompt_tokens": 5, "total_tokens": 5},
                }
            ).encode()
        if url.endswith("/chat/completions"):
            return 200, json.dumps(
                {
                    "model": chat_model,
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": chat_content},
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 8,
                        "total_tokens": 18,
                    },
                }
            ).encode()
        raise AssertionError(url)

    return transport, calls


def test_normalizer_removes_vllm_only_fields_and_maps_no_thinking() -> None:
    request = normalize_siliconflow_chat_request(
        {
            "model": SILICONFLOW_CHAT_MODEL,
            "messages": [{"role": "user", "content": "probe"}],
            "extra_body": {
                "cache_salt": "secret-salt",
                "chat_template_kwargs": {"enable_thinking": False},
            },
        }
    )
    assert request["extra_body"] == {"enable_thinking": False}
    assert "cache_salt" not in json.dumps(request)


def test_normalizer_rejects_non_mapping() -> None:
    with pytest.raises(SiliconFlowProbeError, match="request_invalid"):
        normalize_siliconflow_chat_request([])  # type: ignore[arg-type]


def test_probe_records_public_shape_only_and_never_key(tmp_path: Path) -> None:
    transport, calls = _fake_transport_factory()
    result = run_siliconflow_probe(
        api_key="test-secret",
        output_root=tmp_path,
        transport=transport,
    )
    assert result["status"] == "PASS"
    assert result["formal_main_table_eligible"] is False
    assert result["mutations_performed"] is False
    assert result["credentials_recorded"] is False
    assert result["chat"]["schema_valid"] is True
    assert result["embedding"]["dimension"] == 1024
    assert "test-secret" not in json.dumps(result)
    artifact = json.loads((tmp_path / "SILICONFLOW_PROBE.json").read_text())
    assert artifact["payload_sha256"] == result["payload_sha256"]
    assert "test-secret" not in (tmp_path / "SILICONFLOW_PROBE.json").read_text()
    chat_calls = [call for call in calls if call[1].endswith("/chat/completions")]
    assert len(chat_calls) == 1
    assert chat_calls[0][3]["enable_thinking"] is False


def test_probe_fails_closed_on_embedding_dimension_drift(tmp_path: Path) -> None:
    transport, _calls = _fake_transport_factory(embedding_dimension=3)
    with pytest.raises(SiliconFlowProbeError, match="embedding_dimension_mismatch"):
        run_siliconflow_probe(
            api_key="test-secret",
            output_root=tmp_path,
            transport=transport,
        )
    assert not (tmp_path / "SILICONFLOW_PROBE.json").exists()


def test_probe_fails_closed_on_schema_drift(tmp_path: Path) -> None:
    transport, _calls = _fake_transport_factory(chat_content='{"status":"bad"}')
    with pytest.raises(SiliconFlowProbeError, match="chat_schema_invalid"):
        run_siliconflow_probe(
            api_key="test-secret",
            output_root=tmp_path,
            transport=transport,
        )

