"""One-request Chat Completions judge for the isolated relay lane.

The module reads one selected provider from Codex config, keeps its bearer
credential in memory, and writes only allowlisted/sanitized evidence.  It has
no dependency on the Native Graphiti mainline.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol
from urllib.parse import urlsplit, urlunsplit


DEFAULT_MODEL = "gpt-5.4-mini"
DEFAULT_CONFIG = Path("/home/ly/.codex/config.toml")
DEFAULT_TIMEOUT_S = 180.0
DEFAULT_MAX_RESPONSE_BYTES = 1024 * 1024
LANE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARTIFACT_ROOT = LANE_ROOT / "artifacts" / "simple_judge"
USER_AGENT = "OpenAI/Python 1.0.0"
_ATTEMPT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[^\s,;\"']+")
_URL_QUERY_RE = re.compile(r"(https?://[^\s?#]+)\?[^\s#]*", re.IGNORECASE)


class ProtocolError(RuntimeError):
    """The relay returned a response that is not a Chat Completion object."""


class HttpStatusError(RuntimeError):
    """A non-success HTTP status without retaining the response body."""

    def __init__(self, status_code: int) -> None:
        self.status_code = int(status_code)
        super().__init__(f"HTTP status {self.status_code}")


@dataclass(frozen=True)
class RelayConfig:
    base_url: str
    api_key: str = field(repr=False)
    model: str = DEFAULT_MODEL
    wire_api: str = "chat"
    config_declared_wire_api: str = "chat"
    provider_name: str = "unknown"
    timeout_s: float = DEFAULT_TIMEOUT_S


@dataclass(frozen=True)
class JudgeRequest:
    attempt_id: str
    mode: str
    prompt: str
    max_tokens: int = 256


@dataclass(frozen=True)
class TransportResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes


@dataclass(frozen=True)
class ParsedChatResponse:
    content: str
    model: str
    finish_reason: str | None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cached_tokens: int
    reasoning_tokens: int
    request_id: str | None
    provider_processing_ms: float | None


@dataclass(frozen=True)
class JudgeResult:
    status: str
    attempt_id: str
    artifact_dir: str
    attempt_count: int
    client_observed_latency_ms: float
    error_class: str | None = None
    returned_model: str | None = None
    finish_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ChatTransport(Protocol):
    def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_s: float,
        max_retries: int,
    ) -> TransportResponse: ...


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Turn every redirect into an HTTP error without forwarding credentials."""

    def redirect_request(
        self,
        req: Any,
        fp: Any,
        code: int,
        msg: str,
        headers: Mapping[str, str],
        newurl: str,
    ) -> None:
        return None


class UrllibChatTransport:
    """Synchronous standard-library transport with no retry loop."""

    def __init__(
        self,
        *,
        opener: Any | None = None,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        proxy_policy: str = "direct",
    ) -> None:
        if int(max_response_bytes) <= 0:
            raise ValueError("max_response_bytes must be positive")
        if proxy_policy not in {"direct", "environment"}:
            raise ValueError("proxy_policy must be direct or environment")
        self.proxy_policy = proxy_policy
        proxy_handler = urllib.request.ProxyHandler(
            {} if proxy_policy == "direct" else None
        )
        self.opener = opener or urllib.request.build_opener(
            proxy_handler,
            NoRedirectHandler(),
        )
        self.max_response_bytes = int(max_response_bytes)

    def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_s: float,
        max_retries: int,
    ) -> TransportResponse:
        if max_retries != 0:
            raise ValueError("the bounded judge requires max_retries=0")
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        request = urllib.request.Request(
            url=url,
            data=encoded,
            headers=dict(headers),
            method="POST",
        )
        try:
            with self.opener.open(request, timeout=float(timeout_s)) as response:
                body = response.read(self.max_response_bytes + 1)
                if len(body) > self.max_response_bytes:
                    raise ProtocolError("Chat response exceeds the configured size limit")
                return TransportResponse(
                    status_code=int(response.status),
                    headers={str(key): str(value) for key, value in response.headers.items()},
                    body=body,
                )
        except urllib.error.HTTPError as exc:
            raise HttpStatusError(int(exc.code)) from None


class OpenAISdkChatTransport:
    """OpenAI SDK transport with redirects, environment proxy, and retries off."""

    proxy_policy = "direct_openai_sdk"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_s: float,
        client: Any | None = None,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        self.base_url = _validated_base_url(base_url)
        self.max_response_bytes = int(max_response_bytes)
        if self.max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        self._owns_client = client is None
        if client is None:
            import httpx
            import openai

            http_client = httpx.Client(
                follow_redirects=False,
                trust_env=False,
                timeout=httpx.Timeout(float(timeout_s)),
            )
            client = openai.OpenAI(
                api_key=api_key,
                base_url=self.base_url,
                default_headers={"User-Agent": USER_AGENT},
                max_retries=0,
                timeout=float(timeout_s),
                http_client=http_client,
            )
        self.client = client

    def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_s: float,
        max_retries: int,
    ) -> TransportResponse:
        if max_retries != 0:
            raise ValueError("the bounded judge requires max_retries=0")
        if url != chat_completions_url(self.base_url):
            raise ValueError("SDK transport endpoint mismatch")
        try:
            raw = self.client.chat.completions.with_raw_response.create(**dict(payload))
            parsed = raw.parse()
        except Exception as exc:
            try:
                import openai
            except ImportError:
                raise
            if isinstance(exc, openai.APIStatusError):
                raise HttpStatusError(int(exc.status_code)) from None
            if isinstance(exc, openai.APITimeoutError):
                raise TimeoutError("OpenAI SDK request timed out") from None
            raise
        encoded = parsed.model_dump_json().encode("utf-8")
        if len(encoded) > self.max_response_bytes:
            raise ProtocolError("Chat response exceeds the configured size limit")
        return TransportResponse(
            status_code=int(raw.status_code),
            headers={str(key): str(value) for key, value in raw.headers.items()},
            body=encoded,
        )

    def close(self) -> None:
        if self._owns_client:
            self.client.close()


def _validated_base_url(value: Any) -> str:
    text = str(value or "").strip().rstrip("/")
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("selected provider base_url must be an HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("selected provider base_url cannot contain credentials or query data")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def load_relay_config(
    *,
    config_path: str | Path = DEFAULT_CONFIG,
    environ: Mapping[str, str] | None = None,
    model: str = DEFAULT_MODEL,
    allow_config_wire_override: bool = False,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> RelayConfig:
    """Resolve one Codex provider while keeping its bearer token in memory."""

    path = Path(config_path)
    try:
        with path.open("rb") as handle:
            document = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        raise ValueError("Codex config is unavailable or invalid") from None

    provider_name = str(document.get("model_provider") or "").strip()
    providers = document.get("model_providers")
    provider = providers.get(provider_name) if isinstance(providers, dict) else None
    if not provider_name or not isinstance(provider, dict):
        raise ValueError("active Codex model_provider is missing")

    declared_wire = str(provider.get("wire_api") or "chat").strip().casefold()
    if declared_wire not in {"chat", "chat_completions", "chat-completions"}:
        if not allow_config_wire_override:
            raise ValueError("selected provider is not configured for chat wire API")

    environment = environ if environ is not None else os.environ
    provider_token = str(provider.get("experimental_bearer_token") or "").strip()
    environment_token = str(environment.get("OPENAI_API_KEY") or "").strip()
    api_key = provider_token or environment_token
    if not api_key:
        raise ValueError(
            "selected provider has no experimental_bearer_token and OPENAI_API_KEY is missing"
        )

    selected_model = str(model or "").strip()
    if not selected_model:
        raise ValueError("an explicit Chat model is required")
    if timeout_s <= 0:
        raise ValueError("timeout_s must be positive")
    return RelayConfig(
        base_url=_validated_base_url(provider.get("base_url")),
        api_key=api_key,
        model=selected_model,
        wire_api="chat",
        config_declared_wire_api=declared_wire,
        provider_name=provider_name,
        timeout_s=float(timeout_s),
    )


def chat_completions_url(base_url: str) -> str:
    return _validated_base_url(base_url) + "/chat/completions"


def build_chat_payload(*, config: RelayConfig, request: JudgeRequest) -> dict[str, Any]:
    if request.mode not in {"text", "code"}:
        raise ValueError("judge mode must be text or code")
    if not isinstance(request.prompt, str) or not request.prompt:
        raise ValueError("judge prompt must be non-empty text")
    if not 1 <= int(request.max_tokens) <= 4096:
        raise ValueError("judge max_tokens must be between 1 and 4096")
    return {
        "model": config.model,
        "messages": [{"role": "user", "content": request.prompt}],
        "max_tokens": int(request.max_tokens),
    }


def _integer(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


def parse_chat_response(response: TransportResponse) -> ParsedChatResponse:
    if int(response.status_code) < 200 or int(response.status_code) >= 300:
        raise HttpStatusError(response.status_code)
    try:
        payload = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ProtocolError("Chat response is not valid UTF-8 JSON") from None
    if not isinstance(payload, dict):
        raise ProtocolError("Chat response must be an object")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ProtocolError("Chat response choices are missing")
    message = choices[0].get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise ProtocolError("Chat response assistant content is missing")
    content = str(message["content"])
    if not content:
        raise ProtocolError("Chat response assistant content is empty")

    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    prompt_details = (
        usage.get("prompt_tokens_details")
        if isinstance(usage.get("prompt_tokens_details"), dict)
        else {}
    )
    completion_details = (
        usage.get("completion_tokens_details")
        if isinstance(usage.get("completion_tokens_details"), dict)
        else {}
    )
    lowered_headers = {str(key).casefold(): str(value) for key, value in response.headers.items()}
    request_id = next(
        (
            lowered_headers[name]
            for name in ("x-request-id", "openai-request-id", "request-id")
            if name in lowered_headers
        ),
        None,
    )
    return ParsedChatResponse(
        content=content,
        model=str(payload.get("model") or ""),
        finish_reason=(
            str(choices[0].get("finish_reason"))
            if choices[0].get("finish_reason") is not None
            else None
        ),
        prompt_tokens=_integer(usage.get("prompt_tokens")),
        completion_tokens=_integer(usage.get("completion_tokens")),
        total_tokens=_integer(usage.get("total_tokens")),
        cached_tokens=_integer(prompt_details.get("cached_tokens")),
        reasoning_tokens=_integer(completion_details.get("reasoning_tokens")),
        request_id=request_id,
        provider_processing_ms=_optional_float(lowered_headers.get("openai-processing-ms")),
    )


def _sanitize_text(value: str, secrets: tuple[str, ...]) -> str:
    text = value
    for secret in sorted((item for item in secrets if item), key=len, reverse=True):
        text = text.replace(secret, "<redacted>")
    text = _BEARER_RE.sub("<redacted>", text)
    text = _URL_QUERY_RE.sub(r"\1?<redacted>", text)
    return text


def sanitize_for_artifact(value: Any, *, secrets: tuple[str, ...] = ()) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): sanitize_for_artifact(child, secrets=secrets)
            for key, child in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_for_artifact(child, secrets=secrets) for child in value]
    if isinstance(value, str):
        return _sanitize_text(value, secrets)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _sanitize_text(str(value), secrets)


def resolve_attempt_dir(artifact_root: str | Path, attempt_id: str) -> Path:
    if _ATTEMPT_RE.fullmatch(str(attempt_id or "")) is None:
        raise ValueError("attempt_id must be a bounded path-safe identifier")
    root = Path(artifact_root)
    resolved_root = root.resolve(strict=False)
    candidate = resolved_root / str(attempt_id)
    if candidate.parent != resolved_root:
        raise ValueError("attempt_id escapes artifact_root")
    return candidate


def prepare_attempt_dir(artifact_root: str | Path, attempt_id: str) -> Path:
    """Atomically claim a new attempt directory and reject symlink roots."""

    root = Path(artifact_root)
    if root.is_symlink():
        raise ValueError("artifact_root cannot be a symlink")
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink():
        raise ValueError("artifact_root became a symlink")
    attempt_dir = resolve_attempt_dir(root, attempt_id)
    attempt_dir.mkdir(mode=0o700, exist_ok=False)
    return attempt_dir


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("ascii")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _classify_error(error: BaseException) -> str:
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, HttpStatusError):
        return f"http_{error.status_code}"
    if isinstance(error, ProtocolError):
        return "protocol_error"
    if isinstance(error, urllib.error.URLError):
        reason = getattr(error, "reason", None)
        if isinstance(reason, TimeoutError):
            return "timeout"
        return "transport_error"
    return "client_error"


def run_judge(
    *,
    config: RelayConfig,
    request: JudgeRequest,
    transport: ChatTransport | None = None,
    artifact_root: str | Path,
) -> JudgeResult:
    """Issue exactly one HTTP request and preserve five atomic checkpoints."""

    attempt_dir = prepare_attempt_dir(artifact_root, request.attempt_id)
    payload = build_chat_payload(config=config, request=request)
    endpoint = chat_completions_url(config.base_url)
    active_transport = transport or UrllibChatTransport()
    proxy_policy = str(getattr(active_transport, "proxy_policy", "injected_test_transport"))
    transport_type = type(active_transport).__name__
    max_response_bytes = getattr(active_transport, "max_response_bytes", None)
    secrets = (config.api_key,)
    started_at = _utc_now()
    manifest = {
        "schema_version": "membind.temporary-simple-judge.manifest.v1",
        "attempt_id": request.attempt_id,
        "diagnostic_only": True,
        "mainline_state_advanced": False,
        "model": config.model,
        "provider_name": config.provider_name,
        "endpoint": endpoint,
        "config_declared_wire_api": config.config_declared_wire_api,
        "effective_wire_api": "chat",
        "max_retries": 0,
        "timeout_s": config.timeout_s,
        "timeout_semantics": "socket_operation_timeout_not_hard_end_to_end_deadline",
        "max_response_bytes": max_response_bytes,
        "proxy_policy": proxy_policy,
        "transport_type": transport_type,
        "mode": request.mode,
        "prompt_sha256": _sha256_text(request.prompt),
        "prompt_utf8_bytes": len(request.prompt.encode("utf-8")),
        "client_injected_system_prompt": False,
        "current_run_relay_prompt_injection_observed": "unknown",
        "relay_prompt_injection_prior_suspected": True,
        "relay_prompt_injection_prior_evidence": (
            "historical_minimal_prompt_usage_and_reasoning_content_anomaly"
        ),
        "started_at": started_at,
    }
    _atomic_write_json(
        attempt_dir / "00_manifest.json",
        sanitize_for_artifact(manifest, secrets=secrets),
    )
    _atomic_write_json(
        attempt_dir / "01_request.json",
        sanitize_for_artifact(
            {
                "schema_version": "membind.temporary-simple-judge.request.v1",
                "attempt_id": request.attempt_id,
                "request_envelope_sha256": _sha256_text(
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                ),
                "model": config.model,
                "max_tokens": int(request.max_tokens),
                "message_count": 1,
                "message_roles": ["user"],
                "message_content_sha256": [_sha256_text(request.prompt)],
                "message_content_utf8_bytes": [len(request.prompt.encode("utf-8"))],
            },
            secrets=secrets,
        ),
    )

    parsed: ParsedChatResponse | None = None
    failure: BaseException | None = None
    status_code: int | None = None
    start_ns = time.monotonic_ns()
    try:
        raw = active_transport.post_json(
            url=endpoint,
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
            payload=payload,
            timeout_s=config.timeout_s,
            max_retries=0,
        )
        status_code = int(raw.status_code)
        parsed = parse_chat_response(raw)
        if parsed.model != config.model:
            raise ProtocolError("Chat response model does not match the requested model")
        if parsed.finish_reason != "stop":
            raise ProtocolError("Chat response did not finish completely")
    except BaseException as exc:
        failure = exc
        if isinstance(exc, HttpStatusError):
            status_code = exc.status_code
    latency_ms = (time.monotonic_ns() - start_ns) / 1_000_000

    transport_artifact: dict[str, Any] = {
        "schema_version": "membind.temporary-simple-judge.transport.v1",
        "attempt_id": request.attempt_id,
        "attempt_count": 1,
        "max_retries": 0,
        "http_status": status_code,
        "client_observed_latency_ms": latency_ms,
        "latency_semantics": "caller_observed_api_wait_not_model_execution_time",
    }
    if parsed is not None:
        transport_artifact.update(
            {
                "request_id": parsed.request_id,
                "provider_processing_ms": parsed.provider_processing_ms,
                "usage": {
                    "prompt_tokens": parsed.prompt_tokens,
                    "completion_tokens": parsed.completion_tokens,
                    "total_tokens": parsed.total_tokens,
                    "cached_tokens": parsed.cached_tokens,
                    "reasoning_tokens": parsed.reasoning_tokens,
                },
            }
        )
    if failure is not None:
        transport_artifact.update(
            {
                "error_class": _classify_error(failure),
                "error_code": f"{type(failure).__module__}.{type(failure).__qualname__}",
            }
        )
    _atomic_write_json(
        attempt_dir / "02_transport.json",
        sanitize_for_artifact(transport_artifact, secrets=secrets),
    )

    response_artifact: dict[str, Any] = {
        "schema_version": "membind.temporary-simple-judge.response.v1",
        "attempt_id": request.attempt_id,
        "status": (
            "accepted"
            if parsed is not None and failure is None
            else "rejected"
            if parsed is not None
            else "not_available"
        ),
    }
    if parsed is not None:
        response_artifact.update(
            {
                "content_sha256": _sha256_text(parsed.content),
                "content_utf8_bytes": len(parsed.content.encode("utf-8")),
                "returned_model": parsed.model,
                "finish_reason": parsed.finish_reason,
            }
        )
    _atomic_write_json(
        attempt_dir / "03_response.json",
        sanitize_for_artifact(response_artifact, secrets=secrets),
    )

    final_status = "success" if failure is None else "failed"
    error_class = _classify_error(failure) if failure is not None else None
    summary = {
        "schema_version": "membind.temporary-simple-judge.summary.v1",
        "attempt_id": request.attempt_id,
        "status": final_status,
        "attempt_count": 1,
        "error_class": error_class,
        "returned_model": parsed.model if parsed is not None else None,
        "finish_reason": parsed.finish_reason if parsed is not None else None,
        "client_observed_latency_ms": latency_ms,
        "diagnostic_only": True,
        "mainline_state_advanced": False,
        "completed_at": _utc_now(),
    }
    _atomic_write_json(
        attempt_dir / "04_summary.json",
        sanitize_for_artifact(summary, secrets=secrets),
    )
    return JudgeResult(
        status=final_status,
        attempt_id=request.attempt_id,
        artifact_dir=str(attempt_dir),
        attempt_count=1,
        client_observed_latency_ms=latency_ms,
        error_class=error_class,
        returned_model=parsed.model if parsed is not None else None,
        finish_reason=parsed.finish_reason if parsed is not None else None,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--mode", choices=("text", "code"), default="text")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--timeout-s", type=float, default=DEFAULT_TIMEOUT_S)
    parser.add_argument("--transport", choices=("sdk", "urllib"), default="sdk")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--allow-config-wire-override",
        action="store_true",
        help="Explicitly use Chat Completions even when config declares another wire API.",
    )
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=DEFAULT_ARTIFACT_ROOT,
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        config = load_relay_config(
            config_path=args.config,
            model=args.model,
            allow_config_wire_override=args.allow_config_wire_override,
            timeout_s=args.timeout_s,
        )
        transport: ChatTransport = (
            OpenAISdkChatTransport(
                base_url=config.base_url,
                api_key=config.api_key,
                timeout_s=config.timeout_s,
            )
            if args.transport == "sdk"
            else UrllibChatTransport()
        )
        try:
            result = run_judge(
                config=config,
                request=JudgeRequest(
                    attempt_id=args.attempt_id,
                    mode=args.mode,
                    prompt=args.prompt,
                    max_tokens=args.max_tokens,
                ),
                transport=transport,
                artifact_root=args.artifact_root,
            )
        finally:
            close = getattr(transport, "close", None)
            if callable(close):
                close()
    except BaseException as exc:
        print(
            json.dumps(
                {
                    "status": "failed_before_or_during_attempt",
                    "error_code": f"{type(exc).__module__}.{type(exc).__qualname__}",
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(result.to_dict(), ensure_ascii=True, sort_keys=True))
    return 0 if result.status == "success" else 2


if __name__ == "__main__":
    raise SystemExit(main())
