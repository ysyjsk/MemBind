"""Version-explicit parsing and reduction for process-global vLLM metrics."""

from __future__ import annotations

import math
import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


_COUNTERS = {
    "prefix_cache_queries",
    "prefix_cache_hits",
    "preemptions",
    "prompt_tokens",
    "generation_tokens",
}
_GAUGES = {"running_requests", "waiting_requests", "gpu_kv_cache_usage"}
_PROM_LINE = re.compile(
    r"^(?P<name>[A-Za-z_:][A-Za-z0-9_:]*)(?:\{(?P<labels>[^}]*)\})?\s+"
    r"(?P<value>[-+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][-+]?[0-9]+)?)$"
)
_METRIC_MAP = {
    "vllm:num_requests_running": "running_requests",
    "vllm:num_requests_waiting": "waiting_requests",
    "vllm:kv_cache_usage_perc": "gpu_kv_cache_usage",
    "vllm:prefix_cache_queries_total": "prefix_cache_queries",
    "vllm:prefix_cache_hits_total": "prefix_cache_hits",
    "vllm:num_preemptions_total": "preemptions",
    "vllm:prompt_tokens_total": "prompt_tokens",
    "vllm:generation_tokens_total": "generation_tokens",
}


@dataclass(frozen=True, slots=True)
class PrometheusSnapshot:
    timestamp_ns: int
    values: Mapping[str, float]

    def __post_init__(self) -> None:
        if isinstance(self.timestamp_ns, bool) or not isinstance(self.timestamp_ns, int) or self.timestamp_ns < 0:
            raise ValueError("telemetry timestamp invalid")
        if not isinstance(self.values, Mapping):
            raise ValueError("telemetry values invalid")
        for key, value in self.values.items():
            if not isinstance(key, str) or isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ValueError("telemetry values invalid")


def parse_vllm_prometheus(text: str, *, timestamp_ns: int) -> PrometheusSnapshot:
    if not isinstance(text, str):
        raise ValueError("Prometheus payload invalid")
    values: dict[str, float] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _PROM_LINE.fullmatch(line)
        if match is None or match.group("name") not in _METRIC_MAP:
            continue
        key = _METRIC_MAP[match.group("name")]
        # The pinned deployment has one engine/model series.  Multiple series
        # would make process-global attribution ambiguous and therefore fail.
        if key in values:
            raise ValueError(f"vLLM metric has multiple series: {key}")
        values[key] = float(match.group("value"))
    required = set(_COUNTERS) | set(_GAUGES)
    if set(values) != required:
        raise ValueError(f"vLLM 0.26 metric inventory incomplete: {sorted(required - set(values))}")
    return PrometheusSnapshot(timestamp_ns=timestamp_ns, values=values)


def _open(request: urllib.request.Request, *, timeout_seconds: float = 10.0) -> bytes:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            if int(response.status) != 200:
                raise ValueError(f"vLLM HTTP status invalid: {response.status}")
            return response.read()
    except (OSError, urllib.error.URLError, urllib.error.HTTPError) as error:
        raise ValueError(f"vLLM HTTP request failed: {type(error).__name__}") from None


def fetch_vllm_snapshot(base_url: str) -> PrometheusSnapshot:
    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3]
    payload = _open(urllib.request.Request(f"{root}/metrics", method="GET"))
    try:
        text = payload.decode("utf-8")
    except UnicodeError:
        raise ValueError("vLLM metrics encoding invalid") from None
    return parse_vllm_prometheus(text, timestamp_ns=time.monotonic_ns())


def fetch_vllm_model_identity(
    base_url: str,
    *,
    expected_model: str = "qwen3-32b-fp8",
    expected_max_model_len: int = 65536,
) -> dict[str, object]:
    url = f"{base_url.rstrip('/')}/models"
    payload = _open(urllib.request.Request(url, method="GET"))
    try:
        value = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError):
        raise ValueError("vLLM model identity invalid") from None
    data = value.get("data") if isinstance(value, dict) else None
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        raise ValueError("vLLM model identity invalid")
    model = data[0]
    if model.get("id") != expected_model or model.get("max_model_len") != expected_max_model_len:
        raise ValueError("vLLM model identity drift")
    return {
        "served_model_id": model["id"],
        "max_model_len": model["max_model_len"],
        "system_fingerprint_expected_prefix": "vllm-0.26.0",
    }


def probe_vllm_embedding_cache_salt(
    base_url: str, api_key: str | None, cache_salt: str
) -> dict[str, object]:
    if not isinstance(cache_salt, str) or not 1 <= len(cache_salt) <= 64:
        raise ValueError("cache salt invalid")
    body = json.dumps(
        {
            "model": "qwen3-embedding-0.6b",
            "input": ["cache salt probe"],
            "encoding_format": "float",
            "cache_salt": cache_salt,
        }
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = _open(
        urllib.request.Request(
            f"{base_url.rstrip('/')}/embeddings",
            data=body,
            headers=headers,
            method="POST",
        ),
        timeout_seconds=60.0,
    )
    try:
        value: Any = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError):
        raise ValueError("embedding cache salt probe response invalid") from None
    data = value.get("data") if isinstance(value, dict) else None
    usage = value.get("usage") if isinstance(value, dict) else None
    if not isinstance(data, list) or len(data) != 1 or not isinstance(usage, dict):
        raise ValueError("embedding cache salt probe failed")
    return {
        "status": "EMBEDDING_CACHE_SALT_ACCEPTED",
        "http_status": 200,
        "embedding_count": 1,
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "secrets_persisted": False,
    }


def probe_vllm_cache_salt(base_url: str, api_key: str | None, cache_salt: str) -> dict[str, object]:
    if not isinstance(cache_salt, str) or not 1 <= len(cache_salt) <= 64:
        raise ValueError("cache salt invalid")
    body = json.dumps(
        {
            "model": "qwen3-32b-fp8",
            "messages": [{"role": "user", "content": "Return OK"}],
            "temperature": 0,
            "max_tokens": 4,
            "chat_template_kwargs": {"enable_thinking": False},
            "cache_salt": cache_salt,
        }
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = _open(
        urllib.request.Request(
            f"{base_url.rstrip('/')}/chat/completions",
            data=body,
            headers=headers,
            method="POST",
        ),
        timeout_seconds=60.0,
    )
    try:
        value: Any = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError):
        raise ValueError("cache salt probe response invalid") from None
    choices = value.get("choices") if isinstance(value, dict) else None
    fingerprint = value.get("system_fingerprint") if isinstance(value, dict) else None
    if (
        not isinstance(choices, list)
        or not choices
        or choices[0].get("finish_reason") != "stop"
        or not isinstance(fingerprint, str)
        or not fingerprint.startswith("vllm-0.26.0")
    ):
        raise ValueError("cache salt probe failed")
    return {
        "status": "CACHE_SALT_ACCEPTED",
        "http_status": 200,
        "finish_reason": "stop",
        "system_fingerprint": fingerprint,
        "secrets_persisted": False,
    }


def _percentile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(probability * len(ordered)) - 1)]


def _gauge_summary(samples: Sequence[PrometheusSnapshot], key: str) -> dict[str, float]:
    values = [float(sample.values[key]) for sample in samples if key in sample.values]
    if len(values) != len(samples):
        raise ValueError(f"gauge coverage incomplete: {key}")
    total_duration = samples[-1].timestamp_ns - samples[0].timestamp_ns
    if total_duration <= 0:
        mean = sum(values) / len(values)
        above = float(values[-1] > 0)
    else:
        weighted = 0.0
        above_duration = 0
        for left, right, value in zip(samples, samples[1:], values, strict=False):
            duration = right.timestamp_ns - left.timestamp_ns
            if duration < 0:
                raise ValueError("telemetry timestamps not monotonic")
            weighted += value * duration
            if value > 0:
                above_duration += duration
        mean = weighted / total_duration
        above = above_duration / total_duration
    return {
        "mean": mean,
        "p95": _percentile(values, 0.95),
        "max": max(values),
        "time_above_zero_fraction": above,
    }


def reduce_vllm_telemetry(samples: Sequence[PrometheusSnapshot]) -> dict[str, object]:
    if isinstance(samples, (str, bytes)) or not isinstance(samples, Sequence) or len(samples) < 2:
        raise ValueError("at least two telemetry snapshots required")
    selected = tuple(samples)
    if any(not isinstance(value, PrometheusSnapshot) for value in selected):
        raise ValueError("telemetry snapshot invalid")
    if any(right.timestamp_ns <= left.timestamp_ns for left, right in zip(selected, selected[1:])):
        raise ValueError("telemetry timestamps not monotonic")
    counters: dict[str, float] = {}
    for key in sorted(_COUNTERS):
        values = [float(sample.values[key]) for sample in selected if key in sample.values]
        if not values:
            continue
        if len(values) != len(selected) or any(right < left for left, right in zip(values, values[1:])):
            raise ValueError(f"counter reset or coverage invalid: {key}")
        name = {
            "prefix_cache_queries": "prefix_cache_query_delta",
            "prefix_cache_hits": "prefix_cache_hit_delta",
            "preemptions": "preemption_delta",
            "prompt_tokens": "prompt_token_delta",
            "generation_tokens": "generation_token_delta",
        }[key]
        counters[name] = values[-1] - values[0]
    hit = counters.get("prefix_cache_hit_delta")
    query = counters.get("prefix_cache_query_delta")
    rate = None if hit is None or query is None or query == 0 else hit / query
    gauges = {
        key: _gauge_summary(selected, key)
        for key in sorted(_GAUGES)
        if any(key in sample.values for sample in selected)
    }
    duration_seconds = (selected[-1].timestamp_ns - selected[0].timestamp_ns) / 1_000_000_000
    return {
        "sample_count": len(selected),
        "measured_duration_seconds": duration_seconds,
        "counters": counters,
        "prefix_cache_hit_rate": rate,
        "gauges": gauges,
        "prompt_throughput_tokens_per_second": (
            counters.get("prompt_token_delta", 0.0) / duration_seconds
        ),
        "generation_throughput_tokens_per_second": (
            counters.get("generation_token_delta", 0.0) / duration_seconds
        ),
    }


__all__ = [
    "PrometheusSnapshot",
    "fetch_vllm_model_identity",
    "fetch_vllm_snapshot",
    "parse_vllm_prometheus",
    "probe_vllm_embedding_cache_salt",
    "probe_vllm_cache_salt",
    "reduce_vllm_telemetry",
]
