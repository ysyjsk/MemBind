from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from paper_eval.s2_adapters import (
    OpenAIChatCompletionsTransport,
    S2ChatTransportError,
    S2JudgeAdapterError,
    S2LongMemEvalJudge,
    build_qualified_qwen_judge,
    project_s2_adapter_identity,
)
from paper_eval.s2_live import S2LiveInputs
from paper_eval.s2_reader import OfficialFactsReader
from paper_eval.s2_retrieval_contract import (
    EDGE_SURFACE_CONTRACT,
    validate_retrieval_identity,
)


class _Completions:
    def __init__(self, response: object | None = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def create(self, **request: object) -> object:
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        return self.response


class _Client:
    max_retries = 0

    def __init__(self, completions: _Completions) -> None:
        self.chat = SimpleNamespace(completions=completions)
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def _inputs() -> S2LiveInputs:
    return S2LiveInputs(
        run_id="s2-live-adapter-test",
        history_id="07741c45",
        namespace="pev3-s1-namespace",
        question="Where does Ravi work now?",
        question_date="2024/03/01 (Fri) 12:00",
        question_type="knowledge-update",
        reference_answer="OpenAI",
        answer_session_ids=("session-2",),
    )


@pytest.mark.asyncio
async def test_openai_chat_transport_forwards_exact_request_and_exposes_only_safe_identity() -> None:
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=" OpenAI "))],
        usage=SimpleNamespace(prompt_tokens=41, completion_tokens=3),
    )
    completions = _Completions(response=response)
    client = _Client(completions)
    transport = OpenAIChatCompletionsTransport(
        model="qwen3-32b-fp8",
        base_url="http://private-reader.internal:8000/v1",
        api_key="reader-super-secret",
        timeout_seconds=180,
        client=client,
    )
    request = {
        "model": "qwen3-32b-fp8",
        "messages": [{"role": "user", "content": "raw prompt"}],
        "temperature": 0,
        "max_tokens": 500,
        "n": 1,
    }

    result = await transport.complete(request)

    assert completions.calls == [request]
    assert result.content == " OpenAI "
    assert result.prompt_tokens == 41
    assert result.completion_tokens == 3
    public = transport.public_config
    assert public["sdk_hidden_retries"] == 0
    assert public["max_attempts"] == 1
    assert public["endpoint_identity_sha256"] == hashlib.sha256(
        b"http://private-reader.internal:8000/v1/"
    ).hexdigest()
    serialized = json.dumps(public, sort_keys=True)
    assert "reader-super-secret" not in serialized
    assert "private-reader.internal" not in serialized
    await transport.aclose()
    assert client.closed is True


@pytest.mark.asyncio
async def test_openai_chat_transport_fails_once_with_sanitized_error() -> None:
    completions = _Completions(error=ConnectionError("secret URL and raw prompt"))
    transport = OpenAIChatCompletionsTransport(
        model="qwen3-32b-fp8",
        base_url="http://private-reader.internal:8000/v1/",
        api_key="reader-super-secret",
        client=_Client(completions),
    )

    with pytest.raises(S2ChatTransportError) as captured:
        await transport.complete(
            {
                "model": "qwen3-32b-fp8",
                "messages": [{"role": "user", "content": "raw prompt"}],
                "temperature": 0,
                "max_tokens": 500,
                "n": 1,
            }
        )

    assert completions.calls and len(completions.calls) == 1
    assert "ConnectionError" in str(captured.value)
    assert "secret URL" not in str(captured.value)
    assert "raw prompt" not in str(captured.value)


class _CapturingEvaluator:
    def __init__(self) -> None:
        self.items: list[object] = []

    async def evaluate(self, item: object) -> object:
        self.items.append(item)
        return SimpleNamespace(
            status=SimpleNamespace(value="SUCCESS"),
            label=True,
            scorer="longmemeval_official_get_anscheck_prompt",
            judge_model="qwen3-32b-fp8",
            raw_output="yes",
            normalized_output="YES",
            parse_status="YES",
            retry_count=0,
            error_class=None,
            prompt_hash="a" * 64,
            config_hash="b" * 64,
            metadata={
                "official_compatible_label": True,
                "audit_label": True,
                "parser_disagreement": False,
                "unsafe_free_text": "must not be projected",
            },
        )


class _Backend:
    model = "qwen3-32b-fp8"
    config_hash = "b" * 64

    @property
    def public_config(self) -> dict[str, object]:
        return {
            "backend": "openai_compatible_chat_completions",
            "served_model_name": self.model,
            "endpoint_identity_sha256": "c" * 64,
            "temperature": 0,
            "max_tokens": 10,
            "n": 1,
            "thinking_control": "client_request",
            "effective_enable_thinking": False,
            "max_attempts": 1,
            "timeout_seconds": 30.0,
            "retry_delays_seconds": [0.0],
            "sdk_hidden_retries": 0,
            "api_key": "must-never-survive-whitelist",
        }

    async def aclose(self) -> None:
        return None


class _EvaluationItem:
    def __init__(self, **values: object) -> None:
        self.__dict__.update(values)


@pytest.mark.asyncio
async def test_judge_adapter_builds_official_item_and_projects_no_raw_content() -> None:
    evaluator = _CapturingEvaluator()
    judge = S2LongMemEvalJudge(
        backend=_Backend(),
        evaluator=evaluator,
        evaluation_item_type=_EvaluationItem,
    )
    inputs = _inputs()

    evidence = await judge.evaluate(hypothesis="OpenAI", inputs=inputs)

    assert len(evaluator.items) == 1
    item = evaluator.items[0]
    assert item.benchmark == "longmemeval"
    assert item.question_id == inputs.history_id
    assert item.question_type == "knowledge-update"
    assert item.question == inputs.question
    assert item.reference_answer == inputs.reference_answer
    assert item.hypothesis == "OpenAI"
    assert item.abstention is False
    assert evidence == {
        "status": "SUCCESS",
        "label": True,
        "model": "qwen3-32b-fp8",
        "prompt_sha256": "a" * 64,
        "config_sha256": "b" * 64,
        "output_sha256": hashlib.sha256(b"yes").hexdigest(),
        "output_character_count": 3,
        "output_byte_count": 3,
        "parse_status": "YES",
        "retry_count": 0,
        "error_class": None,
    }
    serialized = json.dumps(evidence, sort_keys=True)
    assert inputs.question not in serialized
    assert inputs.reference_answer not in serialized
    assert "must not be projected" not in serialized


@pytest.mark.asyncio
async def test_judge_adapter_turns_service_error_into_sanitized_failure() -> None:
    class ServiceErrorEvaluator:
        async def evaluate(self, item: object) -> object:
            return SimpleNamespace(
                status=SimpleNamespace(value="SERVICE_ERROR"),
                label=None,
                raw_output="",
                retry_count=0,
                error_class="httpx.ConnectError",
                prompt_hash="a" * 64,
                config_hash="b" * 64,
                metadata={},
            )

    judge = S2LongMemEvalJudge(
        backend=_Backend(),
        evaluator=ServiceErrorEvaluator(),
        evaluation_item_type=_EvaluationItem,
    )

    with pytest.raises(S2JudgeAdapterError) as captured:
        await judge.evaluate(hypothesis="OpenAI", inputs=_inputs())

    assert "httpx.ConnectError" in str(captured.value)
    assert _inputs().question not in str(captured.value)
    assert _inputs().reference_answer not in str(captured.value)


def test_production_judge_factory_lazily_uses_qualified_legacy_classes(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    class Backend(_Backend):
        def __init__(self, **kwargs: object) -> None:
            calls.append(("backend", kwargs))

    class Evaluator:
        def __init__(self, backend: object) -> None:
            calls.append(("evaluator", backend))

    modules = {
        "evaluation.backends.openai_compatible": SimpleNamespace(Qwen3JudgeBackend=Backend),
        "evaluation.benchmarks.longmemeval": SimpleNamespace(LongMemEvalAdapter=Evaluator),
        "evaluation.schemas": SimpleNamespace(EvaluationItem=_EvaluationItem),
    }
    monkeypatch.setattr(
        "paper_eval.s2_adapters.importlib.import_module",
        lambda name: modules[name],
    )

    judge = build_qualified_qwen_judge(
        base_url="http://private-judge.internal:8000/v1",
        api_key="judge-super-secret",
    )

    assert isinstance(judge, S2LongMemEvalJudge)
    assert calls[0] == (
        "backend",
        {
            "base_url": "http://private-judge.internal:8000/v1",
            "api_key": "judge-super-secret",
            "thinking_control": "client_request",
            "max_attempts": 1,
        },
    )
    assert calls[1][0] == "evaluator"


def test_combined_adapter_identity_is_hash_bound_and_contains_no_endpoint_or_secret() -> None:
    transport = OpenAIChatCompletionsTransport(
        model="qwen3-32b-fp8",
        base_url="http://private-reader.internal:8000/v1",
        api_key="reader-super-secret",
        client=_Client(_Completions()),
    )
    reader = OfficialFactsReader(model="qwen3-32b-fp8", transport=transport)
    judge = S2LongMemEvalJudge(
        backend=_Backend(),
        evaluator=_CapturingEvaluator(),
        evaluation_item_type=_EvaluationItem,
    )

    identity = project_s2_adapter_identity(
        reader_transport=transport,
        reader=reader,
        judge=judge,
    )

    assert identity["schema_version"] == (
        "membind.paper-eval-v3.s2-adapter-identity.v2"
    )
    expected_retrieval = {
        **EDGE_SURFACE_CONTRACT.to_identity(),
        "retriever_type": "graphiti-basic-edge",
    }
    assert identity["retrieval"] == expected_retrieval
    assert validate_retrieval_identity(identity["retrieval"]) == expected_retrieval
    body = dict(identity)
    digest = body.pop("identity_sha256")
    encoded = json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
    assert digest == hashlib.sha256(encoded).hexdigest()
    serialized = json.dumps(identity, sort_keys=True)
    for forbidden in (
        "reader-super-secret",
        "private-reader.internal",
        "must-never-survive-whitelist",
        '"api_key"',
        '"base_url"',
    ):
        assert forbidden not in serialized
