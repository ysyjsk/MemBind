"""Private-output Judge adapter tests for the graph-quality overlay only."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from paper_eval.graph_quality_judge import (
    GraphQualityJudgeError,
    GraphQualityPrivateLongMemEvalJudge,
    build_graph_quality_qwen_judge,
)
from paper_eval.quality_terminal_semantics import classify_judge_artifact


class _EvaluationItem:
    def __init__(self, **values: object) -> None:
        self.__dict__.update(values)


def _official_prompt(
    question_type: str,
    question: str,
    reference_answer: str,
    hypothesis: str,
    abstention: bool,
) -> str:
    return "\x00".join(
        (
            question_type,
            question,
            reference_answer,
            hypothesis,
            str(abstention),
        )
    )


class _Backend:
    model = "qwen3-32b-fp8"
    config_hash = "b" * 64

    def __init__(self) -> None:
        self.closed = False

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
            "api_key": "must-never-enter-public-config",
            "raw_endpoint": "http://private-judge.internal:8000/v1",
        }

    async def aclose(self) -> None:
        self.closed = True


class _Evaluator:
    def __init__(self, result: object | None = None) -> None:
        self.items: list[object] = []
        self.result = result or SimpleNamespace(
            status=SimpleNamespace(value="SUCCESS"),
            label=True,
            scorer="longmemeval_official_get_anscheck_prompt",
            judge_model="qwen3-32b-fp8",
            raw_output=" yes. ",
            normalized_output="YES",
            parse_status="YES",
            retry_count=0,
            error_class=None,
            prompt_hash=hashlib.sha256(
                _official_prompt(
                    "knowledge-update",
                    "Where does Ravi work now?",
                    "OpenAI",
                    "OpenAI",
                    False,
                ).encode("utf-8")
            ).hexdigest(),
            config_hash="b" * 64,
            metadata={"unsafe_private_value": "do not project"},
        )

    async def evaluate(self, item: object) -> object:
        self.items.append(item)
        return self.result


def _inputs() -> SimpleNamespace:
    return SimpleNamespace(
        run_id="gq-dev-test",
        history_id="07741c45",
        question_type="knowledge-update",
        question="Where does Ravi work now?",
        reference_answer="OpenAI",
    )


@pytest.mark.asyncio
async def test_private_judge_preserves_raw_output_and_official_item_contract() -> None:
    backend = _Backend()
    evaluator = _Evaluator()
    judge = GraphQualityPrivateLongMemEvalJudge(
        backend=backend,
        evaluator=evaluator,
        evaluation_item_type=_EvaluationItem,
        prompt_builder=_official_prompt,
    )

    result = await judge.evaluate(hypothesis="OpenAI", inputs=_inputs())

    assert len(evaluator.items) == 1
    item = evaluator.items[0]
    assert vars(item) == {
        "item_id": "gq-dev-test:07741c45",
        "benchmark": "longmemeval",
        "question_id": "07741c45",
        "question_type": "knowledge-update",
        "question": "Where does Ravi work now?",
        "reference_answer": "OpenAI",
        "hypothesis": "OpenAI",
        "abstention": False,
    }
    assert result == {
        "status": "SUCCESS",
        "label": True,
        "model": "qwen3-32b-fp8",
        "prompt_sha256": hashlib.sha256(
            _official_prompt(
                "knowledge-update",
                "Where does Ravi work now?",
                "OpenAI",
                "OpenAI",
                False,
            ).encode("utf-8")
        ).hexdigest(),
        "config_sha256": "b" * 64,
        "output_sha256": hashlib.sha256(b" yes. ").hexdigest(),
        "output_character_count": 6,
        "output_byte_count": 6,
        "parse_status": "YES",
        "retry_count": 0,
        "error_class": None,
        "raw_output": " yes. ",
    }
    outcome = classify_judge_artifact(result)
    assert outcome.included is True
    assert outcome.correct is True

    public = judge.public_config
    assert public["implementation"] == (
        "qualified_legacy_longmemeval_private_judge_v1"
    )
    assert public["raw_response_persistence"] == "private_artifact_only"
    assert public["raw_response_in_public_artifact"] is False
    assert public["backend_public_config"]["max_attempts"] == 1
    assert public["backend_public_config"]["sdk_hidden_retries"] == 0
    serialized = json.dumps(public, sort_keys=True)
    assert "must-never-enter-public-config" not in serialized
    assert "private-judge.internal" not in serialized
    assert "do not project" not in serialized
    assert " yes. " not in serialized
    assert judge.config_sha256 == hashlib.sha256(
        json.dumps(
            public,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    ).hexdigest()

    await judge.aclose()
    assert backend.closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"prompt_hash": "not-a-sha"}, "hashes"),
        ({"config_hash": "d" * 64}, "configuration identity"),
        ({"judge_model": "other-model"}, "model identity"),
        ({"status": "UNKNOWN"}, "status"),
        ({"label": None}, "label"),
    ],
)
async def test_private_judge_rejects_invalid_terminal_identity(
    changes: dict[str, object], message: str
) -> None:
    values = vars(_Evaluator().result) | changes
    judge = GraphQualityPrivateLongMemEvalJudge(
        backend=_Backend(),
        evaluator=_Evaluator(SimpleNamespace(**values)),
        evaluation_item_type=_EvaluationItem,
        prompt_builder=_official_prompt,
    )

    with pytest.raises(GraphQualityJudgeError, match=message):
        await judge.evaluate(hypothesis="OpenAI", inputs=_inputs())


@pytest.mark.asyncio
async def test_private_judge_sanitizes_service_failures() -> None:
    result = SimpleNamespace(
        status=SimpleNamespace(value="SERVICE_ERROR"),
        label=None,
        judge_model="qwen3-32b-fp8",
        raw_output="",
        parse_status="NOT_RUN",
        retry_count=0,
        error_class="httpx.ConnectError",
        prompt_hash=hashlib.sha256(
            _official_prompt(
                "knowledge-update",
                "Where does Ravi work now?",
                "OpenAI",
                "OpenAI",
                False,
            ).encode("utf-8")
        ).hexdigest(),
        config_hash="b" * 64,
    )
    judge = GraphQualityPrivateLongMemEvalJudge(
        backend=_Backend(),
        evaluator=_Evaluator(result),
        evaluation_item_type=_EvaluationItem,
        prompt_builder=_official_prompt,
    )

    with pytest.raises(
        GraphQualityJudgeError, match="judge service failed: httpx.ConnectError"
    ) as captured:
        await judge.evaluate(hypothesis="OpenAI", inputs=_inputs())

    assert _inputs().question not in str(captured.value)
    assert _inputs().reference_answer not in str(captured.value)


def test_private_judge_exposes_exact_official_prompt_hash_without_raw_prompt() -> None:
    judge = GraphQualityPrivateLongMemEvalJudge(
        backend=_Backend(),
        evaluator=_Evaluator(),
        evaluation_item_type=_EvaluationItem,
        prompt_builder=_official_prompt,
    )

    observed = judge.exact_prompt_sha256(
        hypothesis="OpenAI",
        inputs=_inputs(),
    )
    expected_prompt = _official_prompt(
        "knowledge-update",
        "Where does Ravi work now?",
        "OpenAI",
        "OpenAI",
        False,
    )

    assert observed == hashlib.sha256(expected_prompt.encode("utf-8")).hexdigest()
    assert expected_prompt not in json.dumps(judge.public_config, sort_keys=True)


def test_private_judge_factory_uses_the_qualified_one_attempt_chain(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    class Backend(_Backend):
        def __init__(self, **kwargs: object) -> None:
            super().__init__()
            calls.append(("backend", kwargs))

    class Evaluator:
        def __init__(self, backend: object) -> None:
            calls.append(("evaluator", backend))

    modules = {
        "evaluation.backends.openai_compatible": SimpleNamespace(
            Qwen3JudgeBackend=Backend
        ),
        "evaluation.benchmarks.longmemeval": SimpleNamespace(
            LongMemEvalAdapter=Evaluator
        ),
        "evaluation.schemas": SimpleNamespace(EvaluationItem=_EvaluationItem),
        "evaluation.vendor.longmemeval_evaluate_qa": SimpleNamespace(
            get_anscheck_prompt=_official_prompt
        ),
    }
    monkeypatch.setattr(
        "paper_eval.graph_quality_judge.importlib.import_module",
        lambda name: modules[name],
    )

    judge = build_graph_quality_qwen_judge(
        base_url="http://private-judge.internal:8000/v1",
        api_key="judge-super-secret",
    )

    assert isinstance(judge, GraphQualityPrivateLongMemEvalJudge)
    assert calls[0] == (
        "backend",
        {
            "base_url": "http://private-judge.internal:8000/v1",
            "api_key": "judge-super-secret",
            "thinking_control": "client_request",
            "max_attempts": 1,
        },
    )
    assert calls[1] == ("evaluator", judge._backend)
