"""Private-output LongMemEval Judge for the graph-quality overlay.

The formal baseline Judge deliberately projects only hashes.  Recovery in the
read-only graph-quality overlay additionally needs the exact Judge response,
so this isolated adapter returns it to the private stage writer while keeping
the public runtime identity content-free.  It uses the same qualified legacy
Qwen backend, LongMemEval rubric adapter, and EvaluationItem construction.
"""

from __future__ import annotations

import hashlib
import importlib
from collections.abc import Mapping
from copy import deepcopy
from typing import Any, Callable

from .artifacts import payload_sha256


class GraphQualityJudgeConfigurationError(ValueError):
    """A graph-quality Judge configuration is incomplete or unqualified."""


class GraphQualityJudgeError(RuntimeError):
    """A sanitized graph-quality Judge execution or response failure."""


_BACKEND_PUBLIC_KEYS = (
    "backend",
    "served_model_name",
    "endpoint_identity_sha256",
    "temperature",
    "max_tokens",
    "n",
    "thinking_control",
    "effective_enable_thinking",
    "max_attempts",
    "timeout_seconds",
    "retry_delays_seconds",
    "sdk_hidden_retries",
)
_REQUIRED_BACKEND_KEYS = frozenset(
    {
        "backend",
        "served_model_name",
        "endpoint_identity_sha256",
        "temperature",
        "max_tokens",
        "n",
        "thinking_control",
        "effective_enable_thinking",
        "max_attempts",
        "sdk_hidden_retries",
    }
)
_HEX_DIGITS = frozenset("0123456789abcdef")


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in _HEX_DIGITS for character in value)
    )


def _error_class(error: BaseException) -> str:
    return f"{type(error).__module__}.{type(error).__name__}"


def _status(value: object) -> str:
    candidate = getattr(value, "value", value)
    if candidate not in {"SUCCESS", "INVALID_OUTPUT", "SERVICE_ERROR"}:
        raise GraphQualityJudgeError("judge response invalid: status")
    return str(candidate)


def _safe_backend_config(backend: Any) -> dict[str, Any]:
    public = getattr(backend, "public_config", None)
    if callable(public):
        public = public()
    if not isinstance(public, Mapping):
        raise GraphQualityJudgeConfigurationError(
            "judge public configuration is missing"
        )
    projected = {
        key: deepcopy(public[key]) for key in _BACKEND_PUBLIC_KEYS if key in public
    }
    if not _REQUIRED_BACKEND_KEYS.issubset(projected):
        raise GraphQualityJudgeConfigurationError(
            "judge public configuration is incomplete"
        )
    if (
        projected["max_attempts"] != 1
        or projected["sdk_hidden_retries"] != 0
        or projected["thinking_control"] != "client_request"
        or projected["effective_enable_thinking"] is not False
    ):
        raise GraphQualityJudgeConfigurationError(
            "judge backend is not the qualified one-attempt Qwen configuration"
        )
    return projected


class GraphQualityPrivateLongMemEvalJudge:
    """One-attempt official-rubric Judge whose raw output remains private."""

    def __init__(
        self,
        *,
        backend: Any,
        evaluator: Any,
        evaluation_item_type: type,
        prompt_builder: Callable[[str, str, str, str, bool], str],
    ) -> None:
        backend_config = _safe_backend_config(backend)
        backend_hash = getattr(backend, "config_hash", None)
        model = getattr(backend, "model", None)
        if not _is_sha256(backend_hash):
            raise GraphQualityJudgeConfigurationError(
                "judge configuration hash is invalid"
            )
        if (
            not isinstance(model, str)
            or not model
            or backend_config["served_model_name"] != model
        ):
            raise GraphQualityJudgeConfigurationError(
                "judge model configuration is inconsistent"
            )
        self._backend = backend
        self._evaluator = evaluator
        self._evaluation_item_type = evaluation_item_type
        if not callable(prompt_builder):
            raise GraphQualityJudgeConfigurationError(
                "LongMemEval prompt builder is missing"
            )
        self._prompt_builder = prompt_builder
        self._backend_hash = backend_hash
        self._model = model
        self._public_config = {
            "schema_version": (
                "membind.paper-eval-v3.graph-quality-private-judge-config.v1"
            ),
            "implementation": "qualified_legacy_longmemeval_private_judge_v1",
            "judge_model": model,
            "judge_config_sha256": backend_hash,
            "backend_public_config": backend_config,
            "backend_class": (
                "evaluation.backends.openai_compatible.Qwen3JudgeBackend"
            ),
            "rubric_adapter_class": (
                "evaluation.benchmarks.longmemeval.LongMemEvalAdapter"
            ),
            "evaluation_item_class": "evaluation.schemas.EvaluationItem",
            "raw_prompt_persisted": False,
            "raw_response_persistence": "private_artifact_only",
            "raw_response_in_public_artifact": False,
        }
        self.config_sha256 = payload_sha256(self._public_config)

    @property
    def public_config(self) -> dict[str, Any]:
        """Return only hash-bound, credential-free configuration metadata."""

        return deepcopy(self._public_config)

    async def evaluate(self, *, hypothesis: str, inputs: Any) -> dict[str, Any]:
        """Evaluate one frozen answer and return its private raw response."""

        if not isinstance(hypothesis, str) or not hypothesis:
            raise GraphQualityJudgeError("judge hypothesis is invalid")
        expected_prompt_sha256 = self.exact_prompt_sha256(
            hypothesis=hypothesis,
            inputs=inputs,
        )
        try:
            item = self._evaluation_item_type(
                item_id=f"{inputs.run_id}:{inputs.history_id}",
                benchmark="longmemeval",
                question_id=inputs.history_id,
                question_type=inputs.question_type,
                question=inputs.question,
                reference_answer=inputs.reference_answer,
                hypothesis=hypothesis,
                abstention=False,
            )
        except Exception as error:
            raise GraphQualityJudgeError(
                f"judge input construction failed: {_error_class(error)}"
            ) from None
        try:
            result = await self._evaluator.evaluate(item)
        except Exception as error:
            raise GraphQualityJudgeError(
                f"judge evaluation failed: {_error_class(error)}"
            ) from None

        status = _status(getattr(result, "status", None))
        prompt_hash = getattr(result, "prompt_hash", None)
        config_hash = getattr(result, "config_hash", None)
        if not _is_sha256(prompt_hash) or not _is_sha256(config_hash):
            raise GraphQualityJudgeError("judge response invalid: hashes")
        if prompt_hash != expected_prompt_sha256:
            raise GraphQualityJudgeError(
                "judge response invalid: prompt identity"
            )
        if config_hash != self._backend_hash:
            raise GraphQualityJudgeError(
                "judge response invalid: configuration identity"
            )
        if getattr(result, "judge_model", None) != self._model:
            raise GraphQualityJudgeError("judge response invalid: model identity")

        retry_count = getattr(result, "retry_count", None)
        if retry_count != 0:
            raise GraphQualityJudgeError("judge response invalid: retry count")
        error_class = getattr(result, "error_class", None)
        if status == "SERVICE_ERROR":
            if (
                not isinstance(error_class, str)
                or not error_class
                or not all(
                    character.isalnum() or character in "_."
                    for character in error_class
                )
            ):
                error_class = "unknown"
            raise GraphQualityJudgeError(
                f"judge service failed: {error_class}"
            )

        label = getattr(result, "label", None)
        if type(label) is not bool:
            raise GraphQualityJudgeError("judge response invalid: label")
        if error_class is not None:
            raise GraphQualityJudgeError("judge response invalid: error state")
        parse_status = getattr(result, "parse_status", None)
        if not isinstance(parse_status, str):
            raise GraphQualityJudgeError("judge response invalid: parse status")
        if status == "SUCCESS" and parse_status != ("YES" if label else "NO"):
            raise GraphQualityJudgeError("judge response invalid: parse status")
        if status == "INVALID_OUTPUT" and parse_status != "INVALID":
            raise GraphQualityJudgeError("judge response invalid: parse status")

        raw_output = getattr(result, "raw_output", None)
        if not isinstance(raw_output, str):
            raise GraphQualityJudgeError("judge response invalid: output")
        output_bytes = raw_output.encode("utf-8")
        return {
            "status": status,
            "label": label,
            "model": self._model,
            "prompt_sha256": prompt_hash,
            "config_sha256": config_hash,
            "output_sha256": hashlib.sha256(output_bytes).hexdigest(),
            "output_character_count": len(raw_output),
            "output_byte_count": len(output_bytes),
            "parse_status": parse_status,
            "retry_count": retry_count,
            "error_class": error_class,
            # The graph-quality stage writer must place this field only under
            # its ignored private runtime root; it is never public config.
            "raw_output": raw_output,
        }

    def exact_prompt_sha256(self, *, hypothesis: str, inputs: Any) -> str:
        """Hash the exact official rubric prompt without exposing its text."""

        if not isinstance(hypothesis, str) or not hypothesis:
            raise GraphQualityJudgeError("judge hypothesis is invalid")
        try:
            prompt = self._prompt_builder(
                inputs.question_type,
                inputs.question,
                inputs.reference_answer,
                hypothesis,
                False,
            )
        except Exception as error:
            raise GraphQualityJudgeError(
                f"judge prompt construction failed: {_error_class(error)}"
            ) from None
        if not isinstance(prompt, str) or not prompt:
            raise GraphQualityJudgeError("judge prompt construction failed")
        return hashlib.sha256(prompt.encode("utf-8")).hexdigest()

    async def aclose(self) -> None:
        close = getattr(self._backend, "aclose", None)
        if callable(close):
            result = close()
            if result is not None and hasattr(result, "__await__"):
                await result


def build_graph_quality_qwen_judge(
    *, base_url: str, api_key: str
) -> GraphQualityPrivateLongMemEvalJudge:
    """Bind the same qualified Qwen/LongMemEval chain with private output."""

    backend_module = importlib.import_module(
        "evaluation.backends.openai_compatible"
    )
    benchmark_module = importlib.import_module(
        "evaluation.benchmarks.longmemeval"
    )
    schema_module = importlib.import_module("evaluation.schemas")
    rubric_module = importlib.import_module(
        "evaluation.vendor.longmemeval_evaluate_qa"
    )
    backend = backend_module.Qwen3JudgeBackend(
        base_url=base_url,
        api_key=api_key,
        thinking_control="client_request",
        max_attempts=1,
    )
    evaluator = benchmark_module.LongMemEvalAdapter(backend)
    return GraphQualityPrivateLongMemEvalJudge(
        backend=backend,
        evaluator=evaluator,
        evaluation_item_type=schema_module.EvaluationItem,
        prompt_builder=rubric_module.get_anscheck_prompt,
    )


__all__ = [
    "GraphQualityJudgeConfigurationError",
    "GraphQualityJudgeError",
    "GraphQualityPrivateLongMemEvalJudge",
    "build_graph_quality_qwen_judge",
]
