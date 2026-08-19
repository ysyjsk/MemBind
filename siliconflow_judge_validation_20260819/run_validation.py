#!/usr/bin/env python3
"""Independent SiliconFlow re-judge of frozen Quality Evaluation v1 answers.

The script is intentionally standard-library only.  It reads the API key from
SILICONFLOW_API_KEY, never serializes it, makes one HTTP attempt per operation,
and stores hashes rather than raw judge responses.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import socket
import sys
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "membind.siliconflow-judge-validation.v1"
DEFAULT_BASE_URL = "https://api.siliconflow.cn/v1"
EXPECTED_HISTORY_IDS = (
    "07741c45",
    "b6019101",
    "6071bd76",
    "a2f3aa27",
)
METHODS = {"u0": "U0", "pc2": "P(C=2)"}
MODEL_PREFERENCE = (
    "Qwen/Qwen3-32B",
    "Qwen/Qwen3-30B-A3B",
    "deepseek-ai/DeepSeek-V3",
    "Qwen/Qwen3-14B",
    "Qwen/Qwen3-8B",
)
EXCLUDED_MODEL_TERMS = (
    "embedding",
    "rerank",
    "stable-diffusion",
    "flux",
    "whisper",
    "speech",
    "audio",
    "vision",
    "vl-",
)


class ValidationError(RuntimeError):
    """Base class for expected validation failures."""


class InputContractError(ValidationError):
    """Frozen inputs do not satisfy the paired evaluation contract."""


class ModelSelectionError(ValidationError):
    """A suitable available model could not be selected."""


class APIRequestError(ValidationError):
    """A sanitized API error safe to serialize to a public artifact."""

    def __init__(self, code: str, stage: str, detail: str | None = None):
        super().__init__(code)
        self.code = code
        self.stage = stage
        self.detail = detail


@dataclasses.dataclass(frozen=True)
class FrozenRecord:
    method: str
    method_dir: str
    history_id: str
    task: str
    question: str
    reference: str
    prediction: str
    original_label: bool
    source_path: str
    source_sha256: str


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Write a JSON artifact atomically without ever broadening its target."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def parse_judge_output(raw_output: str) -> tuple[str, bool | None]:
    """Accept only a bare yes/no, with case/whitespace and one period tolerated."""
    normalized = raw_output.strip().lower()
    if re.fullmatch(r"yes\.?", normalized):
        return "YES", True
    if re.fullmatch(r"no\.?", normalized):
        return "NO", False
    return "INVALID", None


def _successful_bundle(history_dir: Path) -> Path:
    successful: list[Path] = []
    for candidate in sorted(history_dir.glob("attempt-*/private_bundle.json")):
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
            artifact = data["private_artifact"]
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise InputContractError(f"invalid private bundle: {candidate}") from exc
        if artifact.get("judge_result", {}).get("status") == "SUCCESS":
            successful.append(candidate)
    if len(successful) != 1:
        raise InputContractError(
            f"expected exactly one successful private bundle under {history_dir}; "
            f"found {len(successful)}"
        )
    return successful[0]


def _load_method_records(
    source_root: Path,
    method_dir: str,
    expected_history_ids: set[str],
) -> dict[str, FrozenRecord]:
    unit_root = source_root / "units" / method_dir
    if not unit_root.is_dir():
        raise InputContractError(f"missing method directory: {unit_root}")
    observed = {path.name for path in unit_root.iterdir() if path.is_dir()}
    if observed != expected_history_ids:
        raise InputContractError(
            f"{method_dir} history set mismatch: expected {sorted(expected_history_ids)}, "
            f"observed {sorted(observed)}"
        )

    records: dict[str, FrozenRecord] = {}
    for history_id in EXPECTED_HISTORY_IDS:
        if history_id not in expected_history_ids:
            continue
        bundle_path = _successful_bundle(unit_root / history_id)
        raw_bytes = bundle_path.read_bytes()
        bundle = json.loads(raw_bytes)
        artifact = bundle["private_artifact"]
        try:
            task = artifact["question_type"]
            question = artifact["question"]
            reference = artifact["reference_answer"]
            prediction = artifact["predicted_answer"]
            original_label = artifact["judge_result"]["label"]
        except KeyError as exc:
            raise InputContractError(f"missing required field {exc!s}: {bundle_path}") from exc
        if task != "knowledge-update":
            raise InputContractError(
                f"unsupported question_type for {method_dir}/{history_id}: {task!r}"
            )
        if not all(isinstance(item, str) and item for item in (question, reference, prediction)):
            raise InputContractError(f"empty or non-string QA field: {bundle_path}")
        if not isinstance(original_label, bool):
            raise InputContractError(f"original judge label is not boolean: {bundle_path}")
        records[history_id] = FrozenRecord(
            method=METHODS[method_dir],
            method_dir=method_dir,
            history_id=history_id,
            task=task,
            question=question,
            reference=reference,
            prediction=prediction,
            original_label=original_label,
            source_path=str(bundle_path.resolve()),
            source_sha256=hashlib.sha256(raw_bytes).hexdigest(),
        )
    return records


def load_frozen_records(
    source_root: Path,
    *,
    expected_history_ids: set[str] | None = None,
) -> list[FrozenRecord]:
    """Load and validate the exact U0/P(C=2) paired frozen answers."""
    expected = expected_history_ids or set(EXPECTED_HISTORY_IDS)
    by_method = {
        method_dir: _load_method_records(source_root, method_dir, expected)
        for method_dir in METHODS
    }
    if set(by_method["u0"]) != set(by_method["pc2"]):
        raise InputContractError("U0 and P(C=2) history sets are not paired")
    for history_id in sorted(expected):
        u0 = by_method["u0"][history_id]
        pc2 = by_method["pc2"][history_id]
        if u0.question != pc2.question:
            raise InputContractError(f"paired question mismatch for {history_id}")
        if u0.reference != pc2.reference:
            raise InputContractError(f"paired reference mismatch for {history_id}")
        if u0.task != pc2.task:
            raise InputContractError(f"paired task mismatch for {history_id}")
    return [
        by_method[method_dir][history_id]
        for method_dir in METHODS
        for history_id in EXPECTED_HISTORY_IDS
        if history_id in expected
    ]


def build_official_prompt(
    repo_root: Path,
    *,
    task: str,
    question: str,
    reference: str,
    prediction: str,
) -> str:
    """Invoke the pinned vendored LongMemEval function as the rubric source."""
    vendor = (
        repo_root
        / "membind-validation"
        / "src"
        / "evaluation"
        / "vendor"
        / "longmemeval_evaluate_qa.py"
    )
    if not vendor.is_file():
        raise InputContractError(f"pinned LongMemEval rubric missing: {vendor}")
    spec = importlib.util.spec_from_file_location("membind_pinned_longmemeval_qa", vendor)
    if spec is None or spec.loader is None:
        raise InputContractError(f"could not load pinned LongMemEval rubric: {vendor}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.get_anscheck_prompt(
        task=task,
        question=question,
        answer=reference,
        response=prediction,
        abstention=False,
    )


def select_model(available_models: Iterable[str], explicit_model: str | None) -> str:
    models = sorted({model for model in available_models if isinstance(model, str) and model})
    if explicit_model:
        if explicit_model not in models:
            raise ModelSelectionError(f"requested model is not present in GET /models: {explicit_model}")
        return explicit_model
    for preferred in MODEL_PREFERENCE:
        if preferred in models:
            return preferred
    candidates = [
        model
        for model in models
        if any(family in model.lower() for family in ("qwen3", "deepseek"))
        and not any(term in model.lower() for term in EXCLUDED_MODEL_TERMS)
    ]
    if not candidates:
        raise ModelSelectionError("no suitable Qwen3/DeepSeek text model in GET /models")

    def score(model: str) -> tuple[int, int, str]:
        lower = model.lower()
        family = 2 if "qwen3" in lower else 1
        sizes = [int(value) for value in re.findall(r"(\d+)[bB]", model)]
        size = max(sizes, default=0)
        return family, size, model

    return max(candidates, key=score)


def build_chat_payload(model: str, prompt: str) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 16,
        "enable_thinking": False,
    }


def _network_error_code(reason: object) -> tuple[str, str | None]:
    text = str(reason).lower()
    if isinstance(reason, socket.gaierror) or any(
        marker in text
        for marker in ("name or service not known", "temporary failure in name resolution")
    ):
        return "DNS_RESOLUTION_FAILED", "hostname resolution was unavailable"
    if isinstance(reason, ConnectionRefusedError) or "connection refused" in text:
        return "CONNECTION_REFUSED", "connection target refused the request"
    if isinstance(reason, TimeoutError) or "timed out" in text:
        return "NETWORK_TIMEOUT", "network operation timed out"
    if any(marker in text for marker in ("network is unreachable", "operation not permitted")):
        return "NETWORK_RESTRICTED", "network operation was blocked or unreachable"
    return "NETWORK_ERROR", type(reason).__name__


class SiliconFlowClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
        proxy_mode: str = "direct",
    ) -> None:
        if not api_key:
            raise ValidationError("SILICONFLOW_API_KEY is empty")
        self._api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        if proxy_mode == "direct":
            self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        elif proxy_mode == "environment":
            self._opener = urllib.request.build_opener()
        else:
            raise ValidationError(f"unsupported proxy mode: {proxy_mode}")

    def _request(self, method: str, path: str, *, payload: Mapping[str, Any] | None = None) -> Any:
        body = None
        headers = {"Accept": "application/json", "Authorization": f"Bearer {self._api_key}"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=body, headers=headers, method=method
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                response_body = response.read()
        except urllib.error.HTTPError as exc:
            error_body = exc.read()
            raise APIRequestError(
                f"HTTP_{exc.code}",
                path,
                f"response_body_sha256={hashlib.sha256(error_body).hexdigest()}",
            ) from None
        except urllib.error.URLError as exc:
            code, detail = _network_error_code(exc.reason)
            raise APIRequestError(code, path, detail) from None
        except (TimeoutError, OSError) as exc:
            code, detail = _network_error_code(exc)
            raise APIRequestError(code, path, detail) from None
        try:
            return json.loads(response_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise APIRequestError(
                "INVALID_JSON_RESPONSE",
                path,
                f"response_body_sha256={hashlib.sha256(response_body).hexdigest()}",
            ) from None

    def list_models(self) -> list[str]:
        response = self._request("GET", "/models")
        try:
            data = response["data"]
            models = [item["id"] for item in data]
        except (KeyError, TypeError):
            raise APIRequestError("INVALID_MODELS_SCHEMA", "/models") from None
        if not models:
            raise APIRequestError("EMPTY_MODEL_LIST", "/models")
        return models

    def judge(self, model: str, prompt: str) -> tuple[str, dict[str, int | None], str | None]:
        response = self._request(
            "POST", "/chat/completions", payload=build_chat_payload(model, prompt)
        )
        try:
            choice = response["choices"][0]
            raw_output = choice["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise APIRequestError("INVALID_CHAT_SCHEMA", "/chat/completions") from None
        if not isinstance(raw_output, str):
            raise APIRequestError("NON_STRING_CHAT_CONTENT", "/chat/completions")
        raw_usage = response.get("usage") or {}
        usage = {
            key: raw_usage.get(key)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            if isinstance(raw_usage.get(key), int)
        }
        finish_reason = choice.get("finish_reason")
        return raw_output, usage, finish_reason if isinstance(finish_reason, str) else None


def make_result_item(
    *,
    method: str,
    history_id: str,
    model: str,
    prompt: str,
    raw_output: str,
    original_label: bool,
    usage: Mapping[str, int | None],
    finish_reason: str | None,
) -> dict[str, Any]:
    parse_status, label = parse_judge_output(raw_output)
    return {
        "method": method,
        "history_id": history_id,
        "model": model,
        "parse_status": parse_status,
        "label": label,
        "original_label": original_label,
        "agrees_with_original": label == original_label if label is not None else None,
        "prompt_sha256": sha256_text(prompt),
        "response_sha256": sha256_text(raw_output),
        "response_character_count": len(raw_output),
        "finish_reason": finish_reason,
        "usage": dict(usage),
    }


def aggregate(items: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for item in items:
        grouped.setdefault(str(item["method"]), []).append(item)
    summary: dict[str, dict[str, Any]] = {}
    for method, method_items in grouped.items():
        valid = [item for item in method_items if item.get("parse_status") in {"YES", "NO"}]
        correct_count = sum(item.get("label") is True for item in valid)
        agreement_count = sum(
            item.get("label") == item.get("original_label") for item in valid
        )
        valid_count = len(valid)
        summary[method] = {
            "question_count": len(method_items),
            "valid_count": valid_count,
            "invalid_count": len(method_items) - valid_count,
            "correct_count": correct_count,
            "accuracy": correct_count / valid_count if valid_count else None,
            "agreement_with_original_count": agreement_count,
            "agreement_with_original_rate": agreement_count / valid_count if valid_count else None,
        }
    return summary


def _input_manifest(records: Iterable[FrozenRecord], source_root: Path) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "INPUT_MANIFEST",
        "created_at": utc_now(),
        "source_run": str(source_root.resolve()),
        "pairing": {
            "methods": ["U0", "P(C=2)"],
            "history_ids": list(EXPECTED_HISTORY_IDS),
            "question_count_per_method": len(EXPECTED_HISTORY_IDS),
            "abstention": False,
        },
        "records": [
            {
                "method": record.method,
                "history_id": record.history_id,
                "task": record.task,
                "source_path": record.source_path,
                "source_sha256": record.source_sha256,
                "question_sha256": sha256_text(record.question),
                "reference_sha256": sha256_text(record.reference),
                "prediction_sha256": sha256_text(record.prediction),
                "original_label": record.original_label,
            }
            for record in records
        ],
    }


def _failure_payload(
    *,
    code: str,
    stage: str,
    detail: str | None,
    completed_requests: int,
    output_dir: Path,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "FAILURE",
        "status": "FAIL",
        "created_at": utc_now(),
        "failure_code": code,
        "stage": stage,
        "detail": detail,
        "completed_judge_requests": completed_requests,
        "output_dir": str(output_dir.resolve()),
        "api_reached": code.startswith("HTTP_") or code in {
            "INVALID_JSON_RESPONSE",
            "INVALID_MODELS_SCHEMA",
            "EMPTY_MODEL_LIST",
            "INVALID_CHAT_SCHEMA",
            "NON_STRING_CHAT_CONTENT",
        },
        "api_key_serialized": False,
    }


def run(args: argparse.Namespace) -> int:
    repo_root = Path(__file__).resolve().parent.parent
    source_root = args.source_run.resolve()
    output_dir = args.output_dir.resolve()
    records = load_frozen_records(source_root)
    write_json(output_dir / "INPUT_MANIFEST.json", _input_manifest(records, source_root))

    config = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "RUN_CONFIG",
        "created_at": utc_now(),
        "base_url": args.base_url,
        "requested_model": args.model,
        "temperature": 0,
        "max_tokens": 16,
        "enable_thinking": False,
        "proxy_mode": args.proxy_mode,
        "timeout_seconds": args.timeout,
        "http_attempts_per_operation": 1,
        "hidden_retries": 0,
        "rubric_source": str(
            (
                repo_root
                / "membind-validation/src/evaluation/vendor/longmemeval_evaluate_qa.py"
            ).resolve()
        ),
        "api_key_source": "SILICONFLOW_API_KEY environment variable",
        "api_key_serialized": False,
        "development_diagnostic_only": True,
    }
    write_json(output_dir / "RUN_CONFIG.json", config)

    if args.dry_run:
        prompts = [
            build_official_prompt(
                repo_root,
                task=record.task,
                question=record.question,
                reference=record.reference,
                prediction=record.prediction,
            )
            for record in records
        ]
        write_json(
            output_dir / "DRY_RUN.json",
            {
                "schema_version": SCHEMA_VERSION,
                "artifact_type": "DRY_RUN",
                "status": "PASS",
                "prompt_count": len(prompts),
                "prompt_sha256": [sha256_text(prompt) for prompt in prompts],
            },
        )
        return 0

    api_key = os.environ.get("SILICONFLOW_API_KEY", "")
    if not api_key:
        write_json(
            output_dir / "FAILURE.json",
            _failure_payload(
                code="MISSING_API_KEY",
                stage="configuration",
                detail="SILICONFLOW_API_KEY is not set",
                completed_requests=0,
                output_dir=output_dir,
            ),
        )
        return 2

    client = SiliconFlowClient(
        api_key,
        base_url=args.base_url,
        timeout=args.timeout,
        proxy_mode=args.proxy_mode,
    )
    results: list[dict[str, Any]] = []
    try:
        models = client.list_models()
        model = select_model(models, args.model)
        write_json(
            output_dir / "MODEL_PREFLIGHT.json",
            {
                "schema_version": SCHEMA_VERSION,
                "artifact_type": "MODEL_PREFLIGHT",
                "status": "PASS",
                "api_reached": True,
                "available_model_count": len(models),
                "available_model_ids_sha256": sha256_text(
                    json.dumps(sorted(models), ensure_ascii=False, separators=(",", ":"))
                ),
                "selected_model": model,
            },
        )
        for record in records:
            prompt = build_official_prompt(
                repo_root,
                task=record.task,
                question=record.question,
                reference=record.reference,
                prediction=record.prediction,
            )
            raw_output, usage, finish_reason = client.judge(model, prompt)
            results.append(
                make_result_item(
                    method=record.method,
                    history_id=record.history_id,
                    model=model,
                    prompt=prompt,
                    raw_output=raw_output,
                    original_label=record.original_label,
                    usage=usage,
                    finish_reason=finish_reason,
                )
            )
    except APIRequestError as exc:
        if results:
            write_json(
                output_dir / "PARTIAL_RESULTS.json",
                {
                    "schema_version": SCHEMA_VERSION,
                    "artifact_type": "PARTIAL_RESULTS",
                    "status": "INCOMPLETE",
                    "items": results,
                    "summary": aggregate(results),
                },
            )
        code = exc.code
        if exc.stage == "/models" and code in {
            "DNS_RESOLUTION_FAILED",
            "CONNECTION_REFUSED",
            "NETWORK_TIMEOUT",
            "NETWORK_RESTRICTED",
            "NETWORK_ERROR",
        }:
            code = "API_NOT_REACHED_SANDBOX_NETWORK_RESTRICTED"
        write_json(
            output_dir / "FAILURE.json",
            _failure_payload(
                code=code,
                stage=exc.stage,
                detail=exc.detail,
                completed_requests=len(results),
                output_dir=output_dir,
            ),
        )
        return 2
    except ModelSelectionError as exc:
        write_json(
            output_dir / "FAILURE.json",
            _failure_payload(
                code="MODEL_SELECTION_FAILED",
                stage="model_selection",
                detail=str(exc),
                completed_requests=0,
                output_dir=output_dir,
            ),
        )
        return 2

    summary = aggregate(results)
    write_json(
        output_dir / "RESULTS.json",
        {
            "schema_version": SCHEMA_VERSION,
            "artifact_type": "RESULTS",
            "status": "PASS",
            "created_at": utc_now(),
            "claim_scope": "DEVELOPMENT_DIAGNOSTIC_NOT_PAPER_SIGNIFICANCE",
            "judge_request_count": len(results),
            "items": results,
            "summary": summary,
            "focus_case": {
                "history_id": "6071bd76",
                "reason": "prediction says more water while changing from 6 oz to 5 oz; reference says less",
                "labels": {
                    item["method"]: item["label"]
                    for item in results
                    if item["history_id"] == "6071bd76"
                },
            },
        },
    )
    failure_path = output_dir / "FAILURE.json"
    if failure_path.exists():
        failure_path.unlink()
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-run",
        type=Path,
        default=repo_root
        / "paper-eval-v3/artifacts/paper_eval/quality_evaluation_v1/runs/qev1-dev-20260817-001",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=script_dir / "artifacts/siliconflow-validation-20260819-001",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default=None)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--proxy-mode", choices=("direct", "environment"), default="direct"
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return run(args)
    except InputContractError as exc:
        print(f"input contract failure: {exc}", file=sys.stderr)
        return 3
    except ValidationError as exc:
        print(f"validation failure: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
