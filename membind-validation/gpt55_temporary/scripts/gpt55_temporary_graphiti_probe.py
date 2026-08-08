"""Temporary GPT-5.5 Graphiti diagnostic runner.

This wrapper lives outside src/ on purpose: it can test the LabForge/GPT
construction path without changing the frozen vLLM validation lane or advancing
mainline state such as CURRENT_STATE.json.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import os
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable


# The temporary lane is nested below the validation repository; keep imports
# explicit so this wrapper cannot accidentally resolve a sibling mainline tree.
ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))


LANE = "gpt55_temporary_diagnostic"
MAINLINE_NOTICE = "temporary GPT diagnostic only; no mainline stage advancement"
DEFAULT_LOCAL_EMBEDDING_MODEL = "BAAI/bge-m3"
DEFAULT_LOCAL_EMBEDDING_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
DEFAULT_LOCAL_EMBEDDING_CACHE = "/data/predator/ly/Mem/cache/huggingface/hub"
DEFAULT_LOCAL_EMBEDDING_DIM = 1024


async def _noop_service_checker() -> None:
    """Skip the vLLM construction preflight for this GPT-only diagnostic lane."""

    return None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _redacted_preflight_tests(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep only gateway diagnostics that are useful and safe to persist."""

    tests: list[dict[str, Any]] = []
    for item in report.get("tests", []) or []:
        if not isinstance(item, dict):
            continue
        kept: dict[str, Any] = {}
        for key in (
            "name",
            "method",
            "path",
            "authenticated",
            "status",
            "classification",
            "latency_ms",
            "body_sha256",
            "error_type",
        ):
            if key in item:
                kept[key] = item[key]
        tests.append(kept)
    return tests


def summarize_preflight_report(
    report: dict[str, Any],
    artifact: str | Path | None,
) -> dict[str, Any]:
    """Summarize LabForge preflight output without persisting credentials."""

    artifact_text = str(artifact or report.get("artifact") or "")
    if "ok" in report:
        ok = bool(report.get("ok"))
        summary: dict[str, Any] = {
            "ok": ok,
            "artifact": artifact_text,
            "reason": report.get("reason"),
        }
        if report.get("tests"):
            summary["tests"] = _redacted_preflight_tests(report)
        if not ok and summary.get("reason") is None:
            summary["reason"] = "preflight reported ok=false"
        return {key: value for key, value in summary.items() if value is not None}

    tests = _redacted_preflight_tests(report)
    classifications = {
        str(item.get("name")): str(item.get("classification"))
        for item in tests
        if item.get("name") is not None
    }
    chat_ok = classifications.get("chat_model_openai_ua") == "success"
    models_classification = classifications.get("models_authenticated_openai_ua")
    models_reached_application = models_classification in {
        "success",
        "application_reached_invalid_token",
        "model_or_permission_rejected",
        "endpoint_not_found_or_unsupported",
    }
    ok = bool(chat_ok and (models_reached_application or models_classification is None))
    reason = None
    if not chat_ok:
        reason = "chat_model_openai_ua did not succeed"
    elif not models_reached_application and models_classification is not None:
        reason = "models_authenticated_openai_ua did not reach application layer"

    summary = {
        "ok": ok,
        "artifact": artifact_text,
        "reason": reason,
        "tests": tests,
    }
    return {key: value for key, value in summary.items() if value is not None}


def _default_preflight(
    *,
    base_url: str,
    api_key: str,
    model: str,
    output: str | Path,
) -> dict[str, Any]:
    """Run the LabForge gateway probe in-process so CLI use matches unit tests."""

    from labforge_gateway_probe import build_report, default_cases, run_case

    tests = [
        run_case(
            base_url=base_url,
            api_key=api_key,
            name=name,
            method=method,
            path=path,
            payload=payload,
            authenticated=authenticated,
        )
        for name, method, path, payload, authenticated in default_cases(model)
    ]
    report = build_report(
        base_url=base_url,
        api_key=api_key,
        tests=tests,
        proxy={
            name: os.environ.get(name)
            for name in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY")
        },
    )
    output_path = Path(output)
    _write_json(output_path, report)
    report["artifact"] = str(output_path)
    return report


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _build_live_m0_spec(attempt: str, question_id: str) -> dict[str, Any]:
    return {
        "attempt": str(attempt),
        "run_id": f"{attempt}_M0_{question_id}",
        "lane": LANE,
        "mode": "live",
        "method": "M0",
        "question_id": str(question_id),
        "repeat": 0,
    }


def _temporary_llm_config(args: argparse.Namespace) -> Any:
    """Build the Chat Completions config locally for the temporary lane only."""

    from graphiti_core.llm_client.config import LLMConfig
    from graphiti_native import decoding_config_from_env

    decoding = decoding_config_from_env()
    return LLMConfig(
        api_key=str(args.api_key),
        model=str(args.model),
        small_model=str(args.model),
        base_url=str(args.base_url),
        temperature=decoding["temperature"],
        max_tokens=decoding["max_tokens"],
    )


def _build_openai_compatible_embedder(
    args: argparse.Namespace,
    embedding_cache: Any | None = None,
) -> Any:
    """Build the old remote embedding client only when explicitly requested."""

    from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
    from embedding_cache import CachingCountingEmbedder

    embed_base_url = os.environ.get("EMBEDDING_BASE_URL", "http://10.87.5.247:8001/v1")
    if not embed_base_url:
        raise RuntimeError("Set EMBEDDING_BASE_URL to an OpenAI-compatible embedding endpoint")
    return CachingCountingEmbedder(
        OpenAIEmbedder(
            OpenAIEmbedderConfig(
                api_key=os.environ.get("EMBEDDING_API_KEY") or os.environ.get("VLLM_API_KEY"),
                base_url=embed_base_url,
                embedding_model=os.environ.get("EMBEDDING_MODEL", "qwen3-embedding-0.6b"),
                embedding_dim=int(os.environ.get("EMBEDDING_DIM", str(DEFAULT_LOCAL_EMBEDDING_DIM))),
            )
        ),
        persistent_cache=embedding_cache,
    )


def _build_local_bge_m3_embedder(
    args: argparse.Namespace,
    embedding_cache: Any | None = None,
) -> Any:
    """Build the local BGE-M3 adapter from the sibling temporary script folder."""

    from embedding_cache import CachingCountingEmbedder
    from local_embedding_adapter import LocalBgeM3Embedder

    return CachingCountingEmbedder(
        LocalBgeM3Embedder(
            model=str(getattr(args, "local_embedding_model", DEFAULT_LOCAL_EMBEDDING_MODEL)),
            revision=str(
                getattr(args, "local_embedding_revision", DEFAULT_LOCAL_EMBEDDING_REVISION)
            ),
            cache_folder=Path(
                getattr(args, "local_embedding_cache_folder", DEFAULT_LOCAL_EMBEDDING_CACHE)
            ),
            dimension=int(getattr(args, "local_embedding_dim", DEFAULT_LOCAL_EMBEDDING_DIM)),
            batch_size=int(getattr(args, "local_embedding_batch_size", 32)),
        ),
        persistent_cache=embedding_cache,
    )


def _build_temporary_embedder(
    args: argparse.Namespace,
    embedding_cache: Any | None = None,
) -> Any:
    """Select the temporary lane embedding provider without touching src/."""

    provider = str(getattr(args, "embedding_provider", "local_bge_m3"))
    if provider == "local_bge_m3":
        return _build_local_bge_m3_embedder(args, embedding_cache)
    if provider == "openai_compatible":
        return _build_openai_compatible_embedder(args, embedding_cache)
    raise ValueError(f"unsupported temporary embedding provider: {provider}")


def _default_embedding_preflight(
    *,
    args: argparse.Namespace,
    output: str | Path,
) -> dict[str, Any]:
    """Preflight the selected temporary embedding provider before live Graphiti."""

    provider = str(getattr(args, "embedding_provider", "local_bge_m3"))
    if provider != "local_bge_m3":
        return {
            "ok": True,
            "status": "skipped",
            "provider": provider,
            "reason": "local embedding preflight only applies to local_bge_m3",
        }
    from local_embedding_adapter import probe_local_embedding

    return probe_local_embedding(
        output=output,
        model=str(getattr(args, "local_embedding_model", DEFAULT_LOCAL_EMBEDDING_MODEL)),
        revision=str(getattr(args, "local_embedding_revision", DEFAULT_LOCAL_EMBEDDING_REVISION)),
        cache_folder=Path(getattr(args, "local_embedding_cache_folder", DEFAULT_LOCAL_EMBEDDING_CACHE)),
        dimension=int(getattr(args, "local_embedding_dim", DEFAULT_LOCAL_EMBEDDING_DIM)),
        batch_size=min(int(getattr(args, "local_embedding_batch_size", 32)), 2),
    )


def _build_temporary_graphiti_factory(args: argparse.Namespace) -> Callable[..., Any]:
    """Return a Graphiti factory that keeps GPT-specific wiring out of src/."""

    def factory(
        prompt_cache: Any | None = None,
        embedding_cache: Any | None = None,
    ) -> Any:
        from graphiti_core import Graphiti
        from graphiti_core.cross_encoder.openai_reranker_client import (
            OpenAIRerankerClient,
        )
        from deterministic_search import (
            install_edge_query_stabilizer,
            install_edge_search_stabilizer,
            install_node_query_stabilizer,
            install_node_resolution_stabilizer,
        )
        from graphiti_native import QwenVLLMClient, decoding_config_from_env, load_env_file, wrap_prompt_cache
        from model_oracle_audit import CrossEncoderAuditWrapper

        load_env_file()
        install_edge_search_stabilizer()
        install_node_resolution_stabilizer()

        decoding = decoding_config_from_env()
        llm_config = _temporary_llm_config(args)
        llm_client = QwenVLLMClient(
            config=llm_config,
            max_tokens=decoding["max_tokens"],
            structured_output_mode="json_schema",
            vllm_options_enabled=False,
        )
        if prompt_cache is not None:
            llm_client = wrap_prompt_cache(
                llm_client,
                prompt_cache,
                model_revision=str(args.model),
            )

        embedder = _build_temporary_embedder(args, embedding_cache)
        reranker = CrossEncoderAuditWrapper(OpenAIRerankerClient(config=llm_config))
        graphiti = Graphiti(
            uri=os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
            user=os.environ.get("NEO4J_USER", "neo4j"),
            password=os.environ.get("NEO4J_PASSWORD", "password"),
            llm_client=llm_client,
            embedder=embedder,
            cross_encoder=reranker,
            max_coroutines=int(os.environ.get("GRAPHITI_MAX_COROUTINES", "8")),
        )
        install_edge_query_stabilizer(graphiti.driver)
        install_node_query_stabilizer(graphiti.driver)
        return graphiti

    return factory


async def run_temporary_probe(
    args: argparse.Namespace,
    *,
    preflight_fn: Callable[..., dict[str, Any] | Awaitable[dict[str, Any]]] = _default_preflight,
    embedding_preflight_fn: Callable[..., dict[str, Any] | Awaitable[dict[str, Any]]] | None = None,
    run_experiment_fn: Callable[..., Awaitable[dict[str, Any]]] | None = None,
    load_instance_fn: Callable[[str | Path, str], dict[str, Any]] | None = None,
    force_embedding_preflight: bool = False,
) -> dict[str, Any]:
    """Gate a single M0 live Graphiti run behind a GPT-5.5 gateway preflight."""

    artifacts = Path(args.artifacts)
    diagnostics = artifacts / "diagnostics"
    summary_path = diagnostics / f"{args.attempt}_summary.json"
    preflight_artifact = diagnostics / f"{args.attempt}_labforge_preflight.json"

    report = await _maybe_await(
        preflight_fn(
            base_url=args.base_url,
            api_key=args.api_key,
            model=args.model,
            output=preflight_artifact,
        )
    )
    preflight_summary = summarize_preflight_report(
        report,
        artifact=report.get("artifact") or preflight_artifact,
    )

    common_summary: dict[str, Any] = {
        "schema_version": "membind.gpt55_temporary_graphiti_probe.v1",
        "attempt": str(args.attempt),
        "lane": LANE,
        "mainline_scope": MAINLINE_NOTICE,
        "next_allowed_mainline_stage": "none",
        "preflight": preflight_summary,
    }
    if not preflight_summary.get("ok"):
        summary = {
            **common_summary,
            "ok": False,
            "status": "blocked_preflight",
            "reason": preflight_summary.get("reason", "gpt55 preflight failed"),
        }
        _write_json(summary_path, summary)
        return summary

    should_preflight_embedding = force_embedding_preflight or run_experiment_fn is None
    if should_preflight_embedding:
        local_embedding_artifact = diagnostics / f"{args.attempt}_local_embedding_preflight.json"
        preflight = embedding_preflight_fn or _default_embedding_preflight
        embedding_preflight = await _maybe_await(
            preflight(args=args, output=local_embedding_artifact)
        )
        if not embedding_preflight.get("ok"):
            summary = {
                **common_summary,
                "ok": False,
                "status": "blocked_embedding_preflight",
                "reason": embedding_preflight.get("reason", "local embedding preflight failed"),
                "embedding_preflight": {
                    key: value
                    for key, value in embedding_preflight.items()
                    if key not in {"raw_api_key", "authorization"}
                },
            }
            _write_json(summary_path, summary)
            return summary

    if load_instance_fn is None:
        from replay_driver import load_instance as load_instance_fn
    if run_experiment_fn is None:
        from experiment_runner import run_experiment as run_experiment_fn

    instance = load_instance_fn(args.data, args.question_id)
    question_id = str(instance.get("question_id") or args.question_id)
    spec = _build_live_m0_spec(str(args.attempt), question_id)

    run_status = await run_experiment_fn(
        spec,
        instance,
        int(args.arrival_interval_ms),
        artifacts=artifacts,
        graphiti_factory=_build_temporary_graphiti_factory(args),
        service_checker=_noop_service_checker,
    )
    ok = str(run_status.get("status")) == "success"
    summary = {
        **common_summary,
        "ok": ok,
        "status": "success" if ok else "failed",
        "run_id": spec["run_id"],
        "run_status": run_status,
    }
    _write_json(summary_path, summary)
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True)
    parser.add_argument("--question-id", required=True)
    parser.add_argument("--attempt", required=True)
    parser.add_argument("--arrival-interval-ms", type=int, default=0)
    parser.add_argument("--artifacts", default="gpt55_temporary/artifacts")
    parser.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL", ""))
    parser.add_argument("--api-key", default=os.environ.get("OPENAI_API_KEY", ""))
    parser.add_argument("--model", default=os.environ.get("GPT55_MODEL", "gpt-5.5"))
    parser.add_argument(
        "--embedding-provider",
        choices=("local_bge_m3", "openai_compatible"),
        default=os.environ.get("GPT55_EMBEDDING_PROVIDER", "local_bge_m3"),
    )
    parser.add_argument(
        "--local-embedding-model",
        default=os.environ.get("GPT55_LOCAL_EMBEDDING_MODEL", DEFAULT_LOCAL_EMBEDDING_MODEL),
    )
    parser.add_argument(
        "--local-embedding-revision",
        default=os.environ.get("GPT55_LOCAL_EMBEDDING_REVISION", DEFAULT_LOCAL_EMBEDDING_REVISION),
    )
    parser.add_argument(
        "--local-embedding-cache-folder",
        default=os.environ.get("GPT55_LOCAL_EMBEDDING_CACHE", DEFAULT_LOCAL_EMBEDDING_CACHE),
    )
    parser.add_argument(
        "--local-embedding-dim",
        type=int,
        default=int(os.environ.get("GPT55_LOCAL_EMBEDDING_DIM", str(DEFAULT_LOCAL_EMBEDDING_DIM))),
    )
    parser.add_argument(
        "--local-embedding-batch-size",
        type=int,
        default=int(os.environ.get("GPT55_LOCAL_EMBEDDING_BATCH_SIZE", "32")),
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    if not args.base_url:
        raise SystemExit("OPENAI_BASE_URL or --base-url is required")
    if not args.api_key:
        raise SystemExit("OPENAI_API_KEY or --api-key is required")
    result = asyncio.run(run_temporary_probe(args))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if not result.get("ok"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
