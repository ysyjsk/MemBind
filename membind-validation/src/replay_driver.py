"""Command driver for the MemBind validation pilot."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import random
import re
import subprocess
import sys
import time
import traceback
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from current_state_gate import LiveAction, require_live_action
from dataset import build_episodes, freeze_split, load_json_records, records_by_question_id, sha256_file
from experiment_runner import ExperimentRunFailed, run_experiment
from formal_gate import validate_formal_gate
from graphiti_membind import M2_MEMBIND_GO_C8, run_membind_go
from graphiti_native import (
    DEFAULT_CONSTRUCTION_MODEL,
    M0_NATIVE_SERIAL,
    M1_WHOLE_PARALLEL_C8,
    build_qwen_graphiti_from_env,
    load_env_file,
    run_native_serial,
    run_whole_parallel,
)
from integration_gate import graphiti_integration_smoke
from v2_oracle_integration import V2_ORACLE_CACHE_ID, run_v2_oracle_integration
from live_runtime import close as close_graphiti
from canonicalize_graph import compare_canonical_graphs
from retrieval_eval import retrieval_metrics
from analysis_pipeline import analyze_artifacts
from statistics import bootstrap_ci_speedup, decide_go_no_go, geometric_mean, paired_speedups, summarize_episode_metrics
from tracing import JsonlTraceWriter


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
ATTEMPT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
DEFAULT_EXPECTED_VLLM_VERSION = "0.26.0"
DEFAULT_MIN_CONSTRUCTION_CONTEXT_TOKENS = 40_960


def _authorization_checker(args: argparse.Namespace) -> Any:
    """Return the internal test seam or the production CURRENT_STATE checker."""

    return getattr(args, "authorization_checker", require_live_action)


def _require_command_action(
    args: argparse.Namespace,
    action: LiveAction,
    *,
    candidate_id: str | None = None,
) -> None:
    _authorization_checker(args)(action, candidate_id=candidate_id)


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: str | Path, value: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def write_json_atomic(path: str | Path, value: Any) -> None:
    path = Path(path)
    text = json.dumps(
        value, ensure_ascii=False, indent=2, sort_keys=True, default=str
    ) + "\n"
    _write_text_atomic(path, text)


def write_json_exclusive(path: str | Path, value: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(
        value, ensure_ascii=False, indent=2, sort_keys=True, default=str
    ) + "\n"
    with path.open("x", encoding="utf-8") as handle:
        handle.write(text)


def _write_text_atomic(path: str | Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def validate_attempt_id(value: Any, *, kind: str) -> str:
    attempt = str(value)
    if ATTEMPT_ID_RE.fullmatch(attempt) is None:
        raise ValueError(
            f"invalid {kind} attempt {attempt!r}; use letters, digits, underscores, or hyphens"
        )
    return attempt


def evaluate_vllm_runtime_contract(
    models_payload: dict[str, Any],
    version_payload: dict[str, Any],
    *,
    requested_model: str,
    expected_version: str,
    minimum_context_tokens: int,
) -> dict[str, Any]:
    """Evaluate immutable remote runtime fields before any live experiment."""

    model_cards = [
        item for item in models_payload.get("data", []) if isinstance(item, dict)
    ]
    model_ids = [str(item.get("id")) for item in model_cards if item.get("id")]
    selected = next(
        (item for item in model_cards if str(item.get("id")) == requested_model),
        None,
    )
    try:
        max_model_len = int(selected["max_model_len"]) if selected is not None else 0
    except (KeyError, TypeError, ValueError):
        max_model_len = 0
    served_version = str(version_payload.get("version") or "")
    minimum_context_tokens = int(minimum_context_tokens)
    models_ok = selected is not None
    version_ok = bool(served_version) and served_version == expected_version
    context_ok = max_model_len >= minimum_context_tokens
    return {
        "models": model_ids,
        "models_ok": models_ok,
        "vllm_version": served_version,
        "expected_vllm_version": expected_version,
        "version_ok": version_ok,
        "max_model_len": max_model_len,
        "minimum_context_tokens": minimum_context_tokens,
        "context_ok": context_ok,
        "runtime_contract_ok": models_ok and version_ok and context_ok,
    }


def update_construction_blocker(
    environment_dir: str | Path, model_probe: dict[str, Any]
) -> dict[str, Any]:
    """Persist or resolve the construction runtime blocker without losing evidence."""

    environment_dir = Path(environment_dir)
    blocker_path = environment_dir / "construction_context_blocker.json"
    blocker: dict[str, Any] = {}
    if blocker_path.exists():
        try:
            existing = read_json(blocker_path)
            if isinstance(existing, dict):
                blocker.update(existing)
        except (OSError, json.JSONDecodeError):
            pass

    observed = {
        key: model_probe.get(key)
        for key in (
            "models",
            "models_ok",
            "vllm_version",
            "expected_vllm_version",
            "version_ok",
            "max_model_len",
            "minimum_context_tokens",
            "context_ok",
            "runtime_contract_ok",
        )
    }
    now = datetime.now(timezone.utc).isoformat()
    if model_probe.get("runtime_contract_ok") is True:
        blocker.update(
            {
                "status": "resolved",
                "formal_gate_allowed": True,
                "resolved_at": now,
                "resolution_probe": observed,
                "active_reasons": [],
            }
        )
    else:
        reasons = []
        if model_probe.get("models_ok") is not True:
            reasons.append("model_unavailable")
        if model_probe.get("version_ok") is not True:
            reasons.append("vllm_version_mismatch")
        if model_probe.get("context_ok") is not True:
            reasons.append("insufficient_context_window")
        blocker.update(
            {
                "status": "blocked",
                "formal_gate_allowed": False,
                "last_checked_at": now,
                "last_observed_probe": observed,
                "active_reasons": reasons,
            }
        )
    write_json_atomic(blocker_path, blocker)
    return blocker


def mark_interrupted_status(path: str | Path) -> bool:
    """Persist an interrupted status instead of silently skipping the run."""

    path = Path(path)
    if not path.exists():
        return False
    status = read_json(path)
    if status.get("status") in {"success", "failed"}:
        return False
    status["status"] = "failed"
    status["interrupted"] = True
    status["error"] = "run interrupted before completion; artifact retained"
    status["finished_at"] = datetime.now(timezone.utc).isoformat()
    write_json(path, status)
    return True


def run_capture(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False).stdout
    except FileNotFoundError as exc:
        return f"missing executable: {exc.filename}\n"


def cmd_gate(args: argparse.Namespace) -> None:
    checker = _authorization_checker(args)
    checker(LiveAction.ENVIRONMENT_GATE)
    load_env_file()
    out_dir = ARTIFACTS / "environment"
    out_dir.mkdir(parents=True, exist_ok=True)
    pip_freeze = run_capture([sys.executable, "-m", "pip", "freeze"])
    nvidia_smi = run_capture(["nvidia-smi"])
    docker_images = run_capture(["docker", "images", "--digests", "--format", "{{json .}}"])
    (out_dir / "pip_freeze.txt").write_text(pip_freeze, encoding="utf-8")
    (out_dir / "nvidia_smi.txt").write_text(nvidia_smi, encoding="utf-8")
    (out_dir / "docker_images.json").write_text(docker_images, encoding="utf-8")

    graphiti_status = "ok"
    try:
        import graphiti_core  # noqa: F401
    except Exception as exc:
        graphiti_status = repr(exc)

    model_probe = probe_vllm(args.structured_checks, authorization_checker=checker)
    update_construction_blocker(out_dir, model_probe)
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "python": sys.version,
        "cwd": str(ROOT),
        "graphiti_import": graphiti_status,
        "construction_base_url": os.environ.get("CONSTRUCTION_LLM_BASE_URL", "http://10.87.5.247:8000/v1/"),
        "embedding_base_url": os.environ.get("EMBEDDING_BASE_URL", "http://10.87.5.247:8001/v1"),
        "local_protocol_deviations": [
            "User authorized local RTX 3090 instead of protocol RTX PRO 6000 pair.",
            "User authorized vLLM 0.26.0 instead of the original protocol vLLM 0.23.0; the version is frozen uniformly for all methods.",
            "Secrets are read from environment variables and are not written to artifacts.",
            "A structured response truncated at 2048 tokens receives at most one bounded retry at 8192 tokens; all methods share this behavior.",
            "Single-episode extraction constrains every episode_indices JSON-schema field to exactly [0], matching Graphiti's field contract and preventing an unbounded guided-decoding array.",
            "When vLLM reports a context-budget 400, a one-token probe obtains exact prompt usage before retrying within the true remainder with a 32-token margin; inputs are never truncated.",
            "RRF still selects edge-resolution top-K membership; every method canonically presents that selected set in logical-content order before assigning prompt indices.",
        ],
        "model_probe": model_probe,
        "embedding_probe": probe_embedding(authorization_checker=checker),
    }
    write_json(out_dir / "manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


def probe_vllm(
    structured_checks: int,
    *,
    authorization_checker: Any = require_live_action,
) -> dict[str, Any]:
    authorization_checker(LiveAction.MODEL_METADATA)
    load_env_file()
    base_url = os.environ.get("CONSTRUCTION_LLM_BASE_URL", "http://10.87.5.247:8000/v1/").rstrip("/")
    api_key = os.environ.get("CONSTRUCTION_LLM_API_KEY") or os.environ.get("VLLM_API_KEY")
    if not api_key:
        return {"ok": False, "reason": "missing CONSTRUCTION_LLM_API_KEY or VLLM_API_KEY"}
    model = os.environ.get("CONSTRUCTION_LLM_MODEL", DEFAULT_CONSTRUCTION_MODEL)
    expected_version = os.environ.get(
        "CONSTRUCTION_EXPECTED_VLLM_VERSION", DEFAULT_EXPECTED_VLLM_VERSION
    )
    minimum_context_tokens = int(
        os.environ.get(
            "CONSTRUCTION_MIN_CONTEXT_TOKENS",
            str(DEFAULT_MIN_CONSTRUCTION_CONTEXT_TOKENS),
        )
    )
    result: dict[str, Any] = {
        "base_url": base_url,
        "requested_model": model,
        "models_ok": False,
        "expected_vllm_version": expected_version,
        "minimum_context_tokens": minimum_context_tokens,
        "runtime_contract_ok": False,
        "structured_success": 0,
        "structured_checks": structured_checks,
    }
    try:
        headers = {"Authorization": "Bearer " + api_key}
        req = urllib.request.Request(base_url + "/models", headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            models_payload = json.loads(resp.read().decode())
            models_status_ok = 200 <= resp.status < 300
        service_root = base_url[:-3] if base_url.endswith("/v1") else base_url
        req = urllib.request.Request(service_root + "/version", headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            version_payload = json.loads(resp.read().decode())
            version_status_ok = 200 <= resp.status < 300
        result.update(
            evaluate_vllm_runtime_contract(
                models_payload,
                version_payload,
                requested_model=model,
                expected_version=expected_version,
                minimum_context_tokens=minimum_context_tokens,
            )
        )
        result["runtime_contract_ok"] = bool(
            result["runtime_contract_ok"] and models_status_ok and version_status_ok
        )
    except Exception as exc:
        result["models_error"] = repr(exc)
        return result
    if structured_checks <= 0:
        result["ok"] = False
        return result
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Return JSON only."},
            {"role": "user", "content": "Return {\"ok\": true, \"n\": 1}."},
        ],
        "temperature": float(os.environ.get("CONSTRUCTION_TEMPERATURE", "0.0")),
        "top_p": 1.0,
        "max_tokens": 64,
        "seed": 20260806,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "Smoke",
                "schema": {
                    "type": "object",
                    "properties": {"ok": {"type": "boolean"}, "n": {"type": "integer"}},
                    "required": ["ok", "n"],
                },
            },
        },
        "chat_template_kwargs": {"enable_thinking": False},
    }
    for _ in range(structured_checks):
        try:
            data = json.dumps(payload).encode()
            req = urllib.request.Request(
                base_url + "/chat/completions",
                data=data,
                headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = json.loads(resp.read().decode())
            content = body["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            if parsed.get("ok") is True and parsed.get("n") == 1:
                result["structured_success"] += 1
        except Exception as exc:
            result.setdefault("structured_errors", []).append(repr(exc))
    result["ok"] = bool(
        result["runtime_contract_ok"]
        and result["structured_success"] == structured_checks
    )
    return result


def probe_embedding(
    *, authorization_checker: Any = require_live_action
) -> dict[str, Any]:
    authorization_checker(LiveAction.EMBEDDING_IDENTITY)
    load_env_file()
    base_url = os.environ.get("EMBEDDING_BASE_URL", "http://10.87.5.247:8001/v1").rstrip("/")
    api_key = os.environ.get("EMBEDDING_API_KEY") or os.environ.get("VLLM_API_KEY")
    model = os.environ.get("EMBEDDING_MODEL", "qwen3-embedding-0.6b")
    expected_dim = int(os.environ.get("EMBEDDING_DIM", "1024"))
    result: dict[str, Any] = {
        "base_url": base_url,
        "requested_model": model,
        "expected_dimension": expected_dim,
        "models_ok": False,
        "embedding_ok": False,
    }
    if not api_key:
        result["reason"] = "missing EMBEDDING_API_KEY or VLLM_API_KEY"
        return result
    headers = {"Authorization": "Bearer " + api_key, "Content-Type": "application/json"}
    try:
        req = urllib.request.Request(base_url + "/models", headers=headers)
        with urllib.request.urlopen(req, timeout=20) as response:
            models = json.loads(response.read().decode())
        result["models"] = [item.get("id") for item in models.get("data", [])]
        result["models_ok"] = model in result["models"]
        body = json.dumps({"model": model, "input": ["Alice works at Adidas."]}).encode()
        req = urllib.request.Request(base_url + "/embeddings", data=body, headers=headers)
        with urllib.request.urlopen(req, timeout=60) as response:
            payload = json.loads(response.read().decode())
        dimension = len(payload["data"][0]["embedding"])
        result["dimension"] = dimension
        result["embedding_ok"] = dimension == expected_dim
        result["ok"] = result["models_ok"] and result["embedding_ok"]
    except Exception as exc:
        result["error"] = repr(exc)
        result["ok"] = False
    return result


def cmd_split(args: argparse.Namespace) -> None:
    split = freeze_split(Path(args.data), ARTIFACTS / "dataset")
    print(json.dumps(split.__dict__, ensure_ascii=False, indent=2))


async def _run_integration_gate() -> dict[str, Any]:
    graphiti = None
    try:
        graphiti = build_qwen_graphiti_from_env()
        return await graphiti_integration_smoke(graphiti)
    finally:
        await close_graphiti(graphiti)


def cmd_integration(args: argparse.Namespace) -> None:
    _require_command_action(args, LiveAction.NEO4J_INTEGRATION)
    smoke_path = ARTIFACTS / "environment" / "graphiti_smoke.json"
    status_path = ARTIFACTS / "environment" / "integration_gate_status.json"
    try:
        smoke = asyncio.run(_run_integration_gate())
    except Exception as exc:
        smoke = {
            "ok": False,
            "error": repr(exc),
            "traceback_tail": traceback.format_exc().splitlines()[-20:],
        }
        write_json(smoke_path, smoke)
        write_json(
            status_path,
            {
                "ok": False,
                "phase": "integration_gate",
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "graphiti_smoke": smoke,
            },
        )
        raise

    smoke["checked_at"] = datetime.now(timezone.utc).isoformat()
    write_json(smoke_path, smoke)
    manifest_path = ARTIFACTS / "environment" / "manifest.json"
    manifest = read_json(manifest_path) if manifest_path.exists() else {}
    tdd = _unit_test_summary()
    write_json(
        status_path,
        {
            "ok": bool(smoke.get("ok") and tdd["ok"]),
            "phase": "integration_gate",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "graphiti_smoke": smoke,
            "remote_construction_contract": manifest.get("model_probe"),
            "remote_embedding_contract": manifest.get("embedding_probe"),
            "tdd_unit_tests": tdd,
        },
    )
    print(json.dumps(smoke, ensure_ascii=False, indent=2))


def build_run_plan(
    split: dict[str, Any], attempt: str = "formal01"
) -> list[dict[str, Any]]:
    attempt = validate_attempt_id(attempt, kind="formal")
    evaluation = [str(qid) for qid in split["evaluation_question_ids"]]
    if len(evaluation) != 8 or len(set(evaluation)) != 8:
        raise ValueError("formal plan requires exactly 8 evaluation question ids")
    plan: list[dict[str, Any]] = []
    for qid in evaluation:
        plan.append({"attempt": attempt, "lane": "correctness", "method": M0_NATIVE_SERIAL, "mode": "capture", "question_id": qid, "repeat": 0})
        plan.append({"attempt": attempt, "lane": "correctness", "method": M2_MEMBIND_GO_C8, "mode": "replay", "question_id": qid, "repeat": 0})
    for qid in evaluation:
        for method in (M0_NATIVE_SERIAL, M1_WHOLE_PARALLEL_C8, M2_MEMBIND_GO_C8):
            for repeat in (0, 1):
                plan.append({"attempt": attempt, "lane": "performance", "method": method, "mode": "live", "question_id": qid, "repeat": repeat})
    random.Random(20260806).shuffle(plan)
    # Preserve the seeded shuffle as much as possible while enforcing the hard
    # read-only cache dependency for each correctness replay.
    for qid in evaluation:
        capture_index = next(
            index
            for index, item in enumerate(plan)
            if item["question_id"] == qid and item["mode"] == "capture"
        )
        replay_index = next(
            index
            for index, item in enumerate(plan)
            if item["question_id"] == qid and item["mode"] == "replay"
        )
        if capture_index > replay_index:
            capture = plan.pop(capture_index)
            replay_index = next(
                index
                for index, item in enumerate(plan)
                if item["question_id"] == qid and item["mode"] == "replay"
            )
            plan.insert(replay_index, capture)
    for idx, item in enumerate(plan):
        item["run_id"] = f"{attempt}_run_{idx:03d}_{item['lane']}_{item['method']}_{item['question_id']}_r{item['repeat']}"
    capture_ids = {
        item["question_id"]: item["run_id"]
        for item in plan
        if item["mode"] == "capture"
    }
    for item in plan:
        if item["mode"] == "replay":
            item["depends_on"] = capture_ids[item["question_id"]]
    return plan


def validate_formal_plan(plan: list[dict[str, Any]]) -> None:
    """Reject stale or unsafe formal plans before the first live run."""
    errors: list[str] = []
    if len(plan) != 64:
        errors.append(f"run plan must contain 64 runs, found {len(plan)}")
    correctness = [item for item in plan if item.get("lane") == "correctness"]
    performance = [item for item in plan if item.get("lane") == "performance"]
    if len(correctness) != 16:
        errors.append(f"correctness lane must contain 16 runs, found {len(correctness)}")
    if len(performance) != 48:
        errors.append(f"performance lane must contain 48 runs, found {len(performance)}")

    question_ids = {str(item.get("question_id")) for item in plan}
    if len(question_ids) != 8:
        errors.append(
            f"run plan must contain exactly 8 evaluation question ids, found {len(question_ids)}"
        )

    attempts = {str(item.get("attempt")) for item in plan if item.get("attempt")}
    if len(attempts) != 1 or any(not item.get("attempt") for item in plan):
        errors.append("every run must carry the same non-empty formal attempt")
        attempt = None
    else:
        attempt = next(iter(attempts))
        try:
            validate_attempt_id(attempt, kind="formal")
        except ValueError as exc:
            errors.append(str(exc))

    run_ids = [str(item.get("run_id")) for item in plan]
    if len(set(run_ids)) != len(run_ids):
        errors.append("run plan contains duplicate run_id values")
    if attempt is not None and any(
        not run_id.startswith(f"{attempt}_run_") for run_id in run_ids
    ):
        errors.append("every run_id must include the formal attempt prefix")

    expected_distribution = Counter(
        {
            (M0_NATIVE_SERIAL, 0): 8,
            (M0_NATIVE_SERIAL, 1): 8,
            (M1_WHOLE_PARALLEL_C8, 0): 8,
            (M1_WHOLE_PARALLEL_C8, 1): 8,
            (M2_MEMBIND_GO_C8, 0): 8,
            (M2_MEMBIND_GO_C8, 1): 8,
        }
    )
    actual_distribution = Counter(
        (str(item.get("method")), int(item.get("repeat", -1)))
        for item in performance
    )
    if actual_distribution != expected_distribution:
        errors.append(
            "performance method/repeat distribution must contain 8 runs for each of "
            "M0/M1/M2 x repeat 0/1"
        )

    positions = {str(item.get("run_id")): index for index, item in enumerate(plan)}
    captures = {
        str(item.get("question_id")): item
        for item in correctness
        if item.get("method") == M0_NATIVE_SERIAL and item.get("mode") == "capture"
    }
    replays = [
        item
        for item in correctness
        if item.get("method") == M2_MEMBIND_GO_C8 and item.get("mode") == "replay"
    ]
    if len(captures) != 8:
        errors.append(f"correctness lane must contain 8 M0 captures, found {len(captures)}")
    if len(replays) != 8:
        errors.append(f"correctness lane must contain 8 M2 replays, found {len(replays)}")
    dependency_specs = [item for item in plan if "depends_on" in item]
    if len(dependency_specs) != 8 or any(item not in replays for item in dependency_specs):
        errors.append("run plan must contain exactly 8 replay depends_on fields")
    for replay in replays:
        qid = str(replay.get("question_id"))
        dependency = replay.get("depends_on")
        capture = captures.get(qid)
        if not dependency:
            errors.append(f"replay {replay.get('run_id')} is missing depends_on")
            continue
        if capture is None or dependency != capture.get("run_id"):
            errors.append(f"replay {replay.get('run_id')} depends on unknown capture {dependency}")
            continue
        if positions.get(str(capture.get("run_id")), -1) >= positions.get(str(replay.get("run_id")), len(plan)):
            errors.append(f"replay {replay.get('run_id')} appears before its capture dependency")
    if errors:
        raise RuntimeError("invalid formal run plan: " + "; ".join(errors))


def _unit_test_summary() -> dict[str, Any]:
    command = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    match = re.search(r"Ran (\d+) tests?", completed.stdout)
    return {
        "command": " ".join(command),
        "count": int(match.group(1)) if match else 0,
        "ok": completed.returncode == 0,
        "output_tail": completed.stdout.splitlines()[-20:],
    }


def validate_formal_execution_gates(
    artifacts: str | Path,
    data_path: str | Path,
    plan: list[dict[str, Any]],
) -> None:
    """Validate every frozen prerequisite before formal execution mutates Neo4j."""
    artifacts = Path(artifacts)
    data_path = Path(data_path)
    errors: list[str] = []
    strict_report = validate_formal_gate(artifacts, data_path, plan)
    errors.extend(strict_report["failures"])
    try:
        split = read_json(artifacts / "dataset" / "frozen_split.json")
    except Exception as exc:
        split = {}
        errors.append(f"missing frozen split: {exc}")

    try:
        validate_formal_plan(plan)
    except RuntimeError as exc:
        errors.append(str(exc))

    plan_attempts = {
        str(item.get("attempt")) for item in plan if item.get("attempt") is not None
    }
    plan_attempt = next(iter(plan_attempts)) if len(plan_attempts) == 1 else None
    if split and plan_attempt is not None:
        expected_sha = str(split.get("source_sha256", ""))
        actual_sha = sha256_file(data_path) if data_path.exists() else ""
        if not expected_sha or expected_sha != actual_sha:
            errors.append("frozen dataset SHA256 does not match input")
        try:
            expected_plan = build_run_plan(split, attempt=plan_attempt)
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"cannot rebuild run plan from frozen split: {exc}")
        else:
            if plan != expected_plan:
                errors.append("run plan is stale or differs from the frozen split")
    elif split:
        errors.append("run plan does not identify exactly one formal attempt")

    manifest = read_json(artifacts / "environment" / "manifest.json") if (artifacts / "environment" / "manifest.json").exists() else {}
    model_probe = manifest.get("model_probe", {})
    if not (
        model_probe.get("models_ok") is True
        and model_probe.get("runtime_contract_ok") is True
        and int(model_probe.get("structured_checks", 0)) == 20
        and int(model_probe.get("structured_success", 0)) == 20
    ):
        errors.append("construction contract is not 20/20")
    if manifest.get("embedding_probe", {}).get("ok") is not True:
        errors.append("embedding contract is not successful")

    integration_path = artifacts / "environment" / "integration_gate_status.json"
    integration = read_json(integration_path) if integration_path.exists() else {}
    if integration.get("ok") is not True:
        errors.append("Graphiti/Neo4j integration gate is not successful")

    smoke_ok = False
    for path in sorted((artifacts / "smoke").glob("*.json")):
        try:
            candidate = read_json(path)
        except Exception:
            continue
        if candidate.get("ok") is True:
            smoke_ok = True
            break
    if not smoke_ok:
        errors.append("no successful smoke gate artifact exists")

    calibration_path = artifacts / "calibration" / "arrival_interval.json"
    try:
        delta = int(read_json(calibration_path).get("DELTA_MS", 0))
        if delta <= 0:
            raise ValueError("DELTA_MS must be positive")
    except Exception as exc:
        errors.append(f"calibration arrival interval is missing or invalid: {exc}")

    if errors:
        raise RuntimeError("formal execution gate failed: " + " | ".join(errors))


def cmd_plan(args: argparse.Namespace) -> None:
    attempt = validate_attempt_id(args.attempt, kind="formal")
    split = read_json(ARTIFACTS / "dataset" / "frozen_split.json")
    plan = build_run_plan(split, attempt=attempt)
    validate_formal_plan(plan)
    plan_text = "\n".join(
        json.dumps(item, ensure_ascii=False, sort_keys=True) for item in plan
    ) + "\n"
    snapshot = ARTIFACTS / "plans" / f"{attempt}.jsonl"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    try:
        with snapshot.open("x", encoding="utf-8") as handle:
            handle.write(plan_text)
    except FileExistsError as exc:
        raise FileExistsError(
            f"formal plan attempt {attempt} already exists and will not be overwritten: {snapshot}"
        ) from exc
    out = ARTIFACTS / "final" / "run_plan.jsonl"
    _write_text_atomic(out, plan_text)
    write_json_atomic(
        ARTIFACTS / "final" / "run_manifest.json",
        {"attempt": attempt, "plan_snapshot": str(snapshot), "runs": plan},
    )
    print(f"wrote {len(plan)} planned runs for {attempt} to {snapshot} and {out}")


def load_instance(data_path: str | Path, question_id: str) -> dict[str, Any]:
    records = records_by_question_id(load_json_records(data_path))
    return records[question_id]


def load_arrival_interval() -> int:
    path = ARTIFACTS / "calibration" / "arrival_interval.json"
    if not path.exists():
        raise RuntimeError("Missing artifacts/calibration/arrival_interval.json. Run calibrate first.")
    return int(read_json(path)["DELTA_MS"])


async def run_one(
    method: str,
    instance: dict[str, Any],
    run_id: str,
    repeat: int,
    arrival_interval_ms: int,
    *,
    lane: str = "ad_hoc",
    mode: str = "live",
    collect_outputs: bool = True,
    cache_id: str | None = None,
) -> dict[str, Any]:
    spec = {
        "run_id": run_id,
        "lane": lane,
        "mode": mode,
        "method": method,
        "question_id": str(instance["question_id"]),
        "repeat": int(repeat),
    }
    if cache_id is not None:
        spec["cache_id"] = cache_id
    return await run_experiment(
        spec,
        instance,
        arrival_interval_ms,
        artifacts=ARTIFACTS,
        collect_outputs=collect_outputs,
    )


def cmd_run(args: argparse.Namespace) -> None:
    _require_command_action(args, LiveAction.NEO4J_INTEGRATION)
    instance = load_instance(args.data, args.question_id)
    arrival_interval_ms = (
        args.arrival_interval_ms
        if args.arrival_interval_ms is not None
        else load_arrival_interval()
    )
    result = asyncio.run(
        run_one(
            args.method,
            instance,
            args.run_id,
            args.repeat,
            arrival_interval_ms,
            lane=args.lane,
            mode=args.mode,
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_v2_oracle_integration(args: argparse.Namespace) -> None:
    checker = _authorization_checker(args)
    checker(LiveAction.V2_R)
    result = asyncio.run(
        run_v2_oracle_integration(
            artifacts=ARTIFACTS,
            attempt=args.attempt,
            arrival_interval_ms=args.arrival_interval_ms,
            authorization_checker=checker,
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("status") != "success":
        raise SystemExit(1)


def cmd_calibrate(args: argparse.Namespace) -> None:
    _require_command_action(args, LiveAction.CALIBRATION)
    attempt = validate_attempt_id(args.attempt, kind="calibration")
    attempt_dir = ARTIFACTS / "calibration" / "attempts"
    status_path = attempt_dir / f"{attempt}.json"
    latency_path = attempt_dir / f"{attempt}.native_episode_latency.parquet"
    if latency_path.exists():
        raise FileExistsError(
            f"calibration attempt {attempt} already exists and will not be overwritten"
        )
    status: dict[str, Any] = {
        "attempt": attempt,
        "status": "running",
        "run_ids": [],
        "completed_run_ids": [],
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        write_json_exclusive(status_path, status)
    except FileExistsError as exc:
        raise FileExistsError(
            f"calibration attempt {attempt} already exists and will not be overwritten"
        ) from exc

    try:
        split = read_json(ARTIFACTS / "dataset" / "frozen_split.json")
        calibration_qids = [str(qid) for qid in split["calibration_question_ids"]]
        if len(calibration_qids) != 4 or len(set(calibration_qids)) != 4:
            raise ValueError("calibration requires exactly 4 unique question ids")
        records = records_by_question_id(load_json_records(args.data))
        run_ids = [f"{attempt}_calibration_M0_{qid}" for qid in calibration_qids]
        status["run_ids"] = run_ids
        write_json_atomic(status_path, status)
        latency_rows: list[dict[str, Any]] = []
        for qid, run_id in zip(calibration_qids, run_ids):
            result = asyncio.run(
                run_one(
                    M0_NATIVE_SERIAL,
                    records[qid],
                    run_id,
                    0,
                    args.arrival_interval_ms,
                    lane="calibration",
                    mode="live",
                    collect_outputs=False,
                )
            )
            if result.get("status") != "success":
                raise RuntimeError(f"calibration run did not succeed: {run_id}")
            status["completed_run_ids"].append(run_id)
            rows_before = len(latency_rows)
            trace_path = ARTIFACTS / "traces" / f"{run_id}.jsonl"
            for line in trace_path.read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                if row.get("error") is not None:
                    continue
                start = row.get("add_episode_start")
                end = row.get("add_episode_end")
                if start is None or end is None:
                    continue
                latency_rows.append(
                    {
                        "attempt": attempt,
                        "question_id": qid,
                        "run_id": run_id,
                        "source_sequence": int(row["source_sequence"]),
                        "native_episode_service_ms": (int(end) - int(start))
                        / 1_000_000,
                    }
                )
            if len(latency_rows) == rows_before:
                raise RuntimeError(f"no successful calibration latencies for {run_id}")
        if len(status["completed_run_ids"]) != 4:
            raise RuntimeError("not all four calibration runs succeeded")

        import pandas as pd

        calibration_frame = pd.DataFrame(latency_rows)
        latency_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_latency = latency_path.with_suffix(latency_path.suffix + ".tmp")
        calibration_frame.to_parquet(temporary_latency, index=False)
        temporary_latency.replace(latency_path)
        median_latency = float(
            calibration_frame["native_episode_service_ms"].median()
        )
        delta = int(round(median_latency / 100.0) * 100)
        out = {
            "attempt": attempt,
            "run_ids": run_ids,
            "DELTA_MS": max(100, delta),
            "median_native_episode_service_ms": median_latency,
            "successful_episode_count": len(latency_rows),
            "source_artifact": str(latency_path),
            "frozen_at": datetime.now(timezone.utc).isoformat(),
        }
        write_json_atomic(ARTIFACTS / "calibration" / "arrival_interval.json", out)
    except BaseException as exc:
        status["status"] = "failed"
        status["error"] = repr(exc)
        status["finished_at"] = datetime.now(timezone.utc).isoformat()
        write_json_atomic(status_path, status)
        raise

    status["status"] = "success"
    status["arrival_interval"] = out
    status["finished_at"] = datetime.now(timezone.utc).isoformat()
    write_json_atomic(status_path, status)
    print(json.dumps(out, ensure_ascii=False, indent=2))


def _retrieval_with_reference(
    candidate: dict[str, Any],
    reference: dict[str, Any],
) -> dict[str, float]:
    return retrieval_metrics(
        candidate.get("retrieved_episode_ids", []),
        candidate.get("gold_episode_ids", []),
        reference_episode_ids=reference.get("retrieved_episode_ids", []),
    )


def _source_order_diagnostics(trace_path: Path, expected_count: int) -> dict[str, Any]:
    rows = [
        json.loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    sequences = [int(row["source_sequence"]) for row in rows]
    published = sorted(
        (
            (int(row["publish_time"]), int(row["source_sequence"]))
            for row in rows
            if row.get("publish_time") is not None
        ),
        key=lambda item: item[0],
    )
    published_sequences = [sequence for _, sequence in published]
    expected = list(range(expected_count))
    return {
        "episode_count": len(rows),
        "expected_episode_count": expected_count,
        "duplicate_sequences": sorted(
            {sequence for sequence in sequences if sequences.count(sequence) > 1}
        ),
        "missing_sequences": sorted(set(expected) - set(sequences)),
        "publish_sequence": published_sequences,
        "source_order_violation": published_sequences != sorted(published_sequences),
        "exactly_once": sorted(sequences) == expected,
    }


def cmd_smoke(args: argparse.Namespace) -> None:
    _require_command_action(args, LiveAction.V3_R)
    split = read_json(ARTIFACTS / "dataset" / "frozen_split.json")
    question_id = args.question_id or split["evaluation_question_ids"][0]
    instance = load_instance(args.data, question_id)
    attempt = str(args.attempt)
    capture_attempt = str(args.reference_attempt or attempt)
    cache_id = f"smoke_{capture_attempt}_{question_id}"
    run_ids = {
        "M0": f"smoke_{capture_attempt}_M0_{question_id}",
        "M1": f"smoke_{attempt}_M1_{question_id}",
        "M2": f"smoke_{attempt}_M2_{question_id}",
    }
    status_path = ARTIFACTS / "smoke" / f"{attempt}.json"
    status: dict[str, Any] = {
        "ok": False,
        "attempt": attempt,
        "reference_attempt": capture_attempt,
        "question_id": question_id,
        "run_ids": run_ids,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        if args.reference_attempt is None:
            asyncio.run(
                run_one(
                    M0_NATIVE_SERIAL,
                    instance,
                    run_ids["M0"],
                    0,
                    args.arrival_interval_ms,
                    lane="smoke",
                    mode="capture",
                    cache_id=cache_id,
                )
            )
        else:
            reference_status = read_json(ARTIFACTS / "runs" / f"{run_ids['M0']}.json")
            if reference_status.get("status") != "success":
                raise RuntimeError("referenced M0 smoke capture did not succeed")
        asyncio.run(
            run_one(
                M2_MEMBIND_GO_C8,
                instance,
                run_ids["M2"],
                0,
                args.arrival_interval_ms,
                lane="smoke",
                mode="replay",
                cache_id=cache_id,
            )
        )
        asyncio.run(
            run_one(
                M1_WHOLE_PARALLEL_C8,
                instance,
                run_ids["M1"],
                0,
                args.arrival_interval_ms,
                lane="smoke",
                mode="live",
                cache_id=cache_id,
            )
        )

        graphs = {
            method: read_json(ARTIFACTS / "graphs" / f"{run_id}.canonical.json")
            for method, run_id in run_ids.items()
        }
        retrieval = {
            method: read_json(ARTIFACTS / "retrieval" / f"{run_id}.json")
            for method, run_id in run_ids.items()
        }
        episode_count = len(build_episodes(instance))
        status["m2_vs_m0"] = compare_canonical_graphs(graphs["M0"], graphs["M2"])
        status["m1_vs_m0"] = compare_canonical_graphs(graphs["M0"], graphs["M1"])
        status["retrieval"] = {
            "M1_vs_M0": _retrieval_with_reference(retrieval["M1"], retrieval["M0"]),
            "M2_vs_M0": _retrieval_with_reference(retrieval["M2"], retrieval["M0"]),
        }
        status["source_order"] = {
            method: _source_order_diagnostics(
                ARTIFACTS / "traces" / f"{run_id}.jsonl", episode_count
            )
            for method, run_id in run_ids.items()
        }
        m2_run = read_json(ARTIFACTS / "runs" / f"{run_ids['M2']}.json")
        status["unexpected_prompt"] = bool(
            m2_run.get("llm_metrics", {}).get("unexpected_prompt", False)
        )
        status["ok"] = (
            status["m2_vs_m0"]["canonical_graph_parity"]
            and not status["unexpected_prompt"]
            and status["source_order"]["M2"]["exactly_once"]
            and not status["source_order"]["M2"]["source_order_violation"]
        )
        if not status["ok"]:
            raise RuntimeError("smoke M2 did not preserve the frozen correctness invariants")
    except Exception as exc:
        status["error"] = repr(exc)
        status["traceback_tail"] = traceback.format_exc().splitlines()[-30:]
        raise
    finally:
        status["finished_at"] = datetime.now(timezone.utc).isoformat()
        write_json(status_path, status)
    print(json.dumps(status, ensure_ascii=False, indent=2))


def cmd_v3_smoke(args: argparse.Namespace) -> None:
    _require_command_action(args, LiveAction.V3_R)
    split = read_json(ARTIFACTS / "dataset" / "frozen_split.json")
    question_id = args.question_id or split["evaluation_question_ids"][0]
    instance = load_instance(args.data, question_id)
    attempt = str(args.attempt)
    capture_attempt = str(args.reference_attempt or attempt)
    cache_id = f"v3_smoke_{capture_attempt}_{question_id}"
    run_ids = {
        "M0": f"v3_smoke_{capture_attempt}_M0_{question_id}",
        "M2": f"v3_smoke_{attempt}_M2_{question_id}",
    }
    status_path = ARTIFACTS / "smoke" / f"{attempt}.json"
    status: dict[str, Any] = {
        "ok": False,
        "stage": "V3",
        "attempt": attempt,
        "reference_attempt": capture_attempt,
        "question_id": question_id,
        "run_ids": run_ids,
        "cache_id": cache_id,
        "forbidden_methods": [M1_WHOLE_PARALLEL_C8],
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        if args.reference_attempt is None:
            asyncio.run(
                run_one(
                    M0_NATIVE_SERIAL,
                    instance,
                    run_ids["M0"],
                    0,
                    args.arrival_interval_ms,
                    lane="v3_smoke",
                    mode="capture",
                    cache_id=cache_id,
                )
            )
        else:
            reference_status = read_json(ARTIFACTS / "runs" / f"{run_ids['M0']}.json")
            if reference_status.get("status") != "success":
                raise RuntimeError("referenced V3 M0 smoke capture did not succeed")
        asyncio.run(
            run_one(
                M2_MEMBIND_GO_C8,
                instance,
                run_ids["M2"],
                0,
                args.arrival_interval_ms,
                lane="v3_smoke",
                mode="replay",
                cache_id=cache_id,
            )
        )

        graphs = {
            method: read_json(ARTIFACTS / "graphs" / f"{run_id}.canonical.json")
            for method, run_id in run_ids.items()
        }
        retrieval = {
            method: read_json(ARTIFACTS / "retrieval" / f"{run_id}.json")
            for method, run_id in run_ids.items()
        }
        episode_count = len(build_episodes(instance))
        status["m2_vs_m0"] = compare_canonical_graphs(graphs["M0"], graphs["M2"])
        status["retrieval"] = {
            "M2_vs_M0": _retrieval_with_reference(retrieval["M2"], retrieval["M0"]),
        }
        status["source_order"] = {
            method: _source_order_diagnostics(
                ARTIFACTS / "traces" / f"{run_id}.jsonl", episode_count
            )
            for method, run_id in run_ids.items()
        }
        m2_run = read_json(ARTIFACTS / "runs" / f"{run_ids['M2']}.json")
        status["unexpected_prompt"] = bool(
            m2_run.get("llm_metrics", {}).get("unexpected_prompt", False)
        )
        status["ok"] = (
            status["m2_vs_m0"]["canonical_graph_parity"]
            and not status["unexpected_prompt"]
            and status["source_order"]["M2"]["exactly_once"]
            and not status["source_order"]["M2"]["source_order_violation"]
        )
        if not status["ok"]:
            raise RuntimeError("V3 smoke M2 did not preserve the frozen correctness invariants")
    except Exception as exc:
        status["error"] = repr(exc)
        status["traceback_tail"] = traceback.format_exc().splitlines()[-30:]
        raise
    finally:
        status["finished_at"] = datetime.now(timezone.utc).isoformat()
        write_json(status_path, status)
    print(json.dumps(status, ensure_ascii=False, indent=2))


def _formal_plan() -> list[dict[str, Any]]:
    path = ARTIFACTS / "final" / "run_plan.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _persist_formal_execution(plan: list[dict[str, Any]]) -> dict[str, Any]:
    import pandas as pd

    rows = []
    for spec in plan:
        status_path = ARTIFACTS / "runs" / f"{spec['run_id']}.json"
        status = read_json(status_path) if status_path.exists() else {"status": "pending"}
        row = {**spec, **status}
        for key, value in list(row.items()):
            if isinstance(value, (dict, list)):
                row[key] = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        rows.append(row)
    frame = pd.DataFrame(rows)
    manifest_path = ARTIFACTS / "final" / "run_manifest.parquet"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(manifest_path, index=False)
    counts = frame["status"].value_counts().to_dict()
    summary = {
        "planned_runs": len(plan),
        "status_counts": {str(key): int(value) for key, value in counts.items()},
        "manifest": str(manifest_path),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "complete": int(counts.get("success", 0)) + int(counts.get("failed", 0)) == len(plan),
    }
    write_json(ARTIFACTS / "final" / "formal_execution_status.json", summary)
    return summary


def cmd_execute(args: argparse.Namespace) -> None:
    _require_command_action(args, LiveAction.FORMAL)
    plan = _formal_plan()
    validate_formal_execution_gates(ARTIFACTS, args.data, plan)
    records = records_by_question_id(load_json_records(args.data))
    arrival_interval_ms = load_arrival_interval()
    attempted = 0
    for spec in plan:
        status_path = ARTIFACTS / "runs" / f"{spec['run_id']}.json"
        if status_path.exists():
            existing = read_json(status_path)
            existing_status = existing.get("status")
            if existing_status in {"success", "failed"}:
                continue
            # A prior process that left a running/unknown status must not be
            # silently treated as a successful completed run on resume.
            mark_interrupted_status(status_path)
            attempted += 1
            _persist_formal_execution(plan)
            continue
        if args.max_runs is not None and attempted >= args.max_runs:
            break
        dependency = spec.get("depends_on")
        if dependency:
            dependency_path = ARTIFACTS / "runs" / f"{dependency}.json"
            dependency_status = read_json(dependency_path) if dependency_path.exists() else {}
            if dependency_status.get("status") != "success":
                write_json(
                    status_path,
                    {
                        **spec,
                        "status": "failed",
                        "error": f"correctness dependency did not succeed: {dependency}",
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
                attempted += 1
                _persist_formal_execution(plan)
                continue
        try:
            asyncio.run(
                run_experiment(
                    spec,
                    records[str(spec["question_id"])],
                    arrival_interval_ms,
                    artifacts=ARTIFACTS,
                )
            )
        except ExperimentRunFailed:
            if args.stop_on_failure:
                _persist_formal_execution(plan)
                raise
        attempted += 1
        _persist_formal_execution(plan)
    summary = _persist_formal_execution(plan)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def iter_trace_rows() -> list[dict[str, Any]]:
    rows = []
    for path in sorted((ARTIFACTS / "traces").glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = sorted({k for row in rows for k in row})
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def cmd_analyze(args: argparse.Namespace) -> None:
    summary = analyze_artifacts(ARTIFACTS, bootstrap_samples=args.bootstrap_samples)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    gate = sub.add_parser("gate")
    gate.add_argument("--structured-checks", type=int, default=20)
    gate.set_defaults(func=cmd_gate)
    integration = sub.add_parser("integration")
    integration.set_defaults(func=cmd_integration)
    split = sub.add_parser("split")
    split.add_argument("--data", required=True)
    split.set_defaults(func=cmd_split)
    plan = sub.add_parser("plan")
    plan.add_argument("--attempt", default="formal01")
    plan.set_defaults(func=cmd_plan)
    calibrate = sub.add_parser("calibrate")
    calibrate.add_argument("--data", required=True)
    calibrate.add_argument("--arrival-interval-ms", type=int, default=0)
    calibrate.add_argument("--attempt", default="calibration01")
    calibrate.set_defaults(func=cmd_calibrate)
    smoke = sub.add_parser("smoke")
    smoke.add_argument("--data", required=True)
    smoke.add_argument("--question-id")
    smoke.add_argument("--attempt", default="smoke01")
    smoke.add_argument("--reference-attempt")
    smoke.add_argument("--arrival-interval-ms", type=int, default=0)
    smoke.set_defaults(func=cmd_smoke)
    v3_smoke = sub.add_parser("v3-smoke")
    v3_smoke.add_argument("--data", required=True)
    v3_smoke.add_argument("--question-id")
    v3_smoke.add_argument("--attempt", default="v3smoke01")
    v3_smoke.add_argument("--reference-attempt")
    v3_smoke.add_argument("--arrival-interval-ms", type=int, default=0)
    v3_smoke.set_defaults(func=cmd_v3_smoke)
    execute = sub.add_parser("execute")
    execute.add_argument("--data", required=True)
    execute.add_argument("--max-runs", type=int)
    execute.add_argument("--stop-on-failure", action="store_true")
    execute.set_defaults(func=cmd_execute)
    run = sub.add_parser("run")
    run.add_argument("--data", required=True)
    run.add_argument("--question-id", required=True)
    run.add_argument("--method", choices=[M0_NATIVE_SERIAL, M1_WHOLE_PARALLEL_C8, M2_MEMBIND_GO_C8], required=True)
    run.add_argument("--run-id", required=True)
    run.add_argument("--repeat", type=int, default=0)
    run.add_argument("--arrival-interval-ms", type=int)
    run.add_argument("--lane", default="ad_hoc")
    run.add_argument("--mode", choices=["live", "capture", "replay"], default="live")
    run.set_defaults(func=cmd_run)
    v2 = sub.add_parser("v2-oracle-integration")
    v2.add_argument("--attempt", default=V2_ORACLE_CACHE_ID)
    v2.add_argument("--arrival-interval-ms", type=int, default=0)
    v2.set_defaults(func=cmd_v2_oracle_integration)
    analyze = sub.add_parser("analyze")
    analyze.add_argument("--bootstrap-samples", type=int, default=10_000)
    analyze.set_defaults(func=cmd_analyze)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
