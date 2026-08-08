"""Temporary LabForge/OpenAI-compatible gateway diagnostic.

This script is intentionally outside src/ so it can support one-off GPT gateway
checks without changing the frozen vLLM experiment path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit


DEFAULT_USER_AGENT = "OpenAI/Python 1.0.0"
_DEFAULT_OUTPUT = (
    "gpt55_temporary/artifacts/diagnostics/"
    "labforge_api_diagnostic_20260808.json"
)
SAFE_RESPONSE_HEADERS = {
    "content-type",
    "content-length",
    "server",
    "via",
    "cf-ray",
    "x-request-id",
    "openai-processing-ms",
}


def default_output_path() -> str:
    """Return the safe, lane-local default for a CLI diagnostic artifact."""

    return _DEFAULT_OUTPUT


def build_headers(
    *,
    api_key: str,
    authenticated: bool,
    has_body: bool,
    user_agent: str = DEFAULT_USER_AGENT,
) -> dict[str, str]:
    headers = {"User-Agent": user_agent}
    if has_body:
        headers["Content-Type"] = "application/json"
    if authenticated:
        headers["Authorization"] = "Bearer " + api_key
    return headers


def safe_report_header_keys(headers: dict[str, str]) -> list[str]:
    return sorted(key.lower() for key in headers)


def classify_response(status: int | None, body_preview: str, headers: dict[str, str]) -> str:
    server = (headers.get("server") or headers.get("Server") or "").casefold()
    body = body_preview.casefold()
    if status == 403 and "cloudflare" in server and "error code: 1010" in body:
        return "cloudflare_waf_or_user_agent_block"
    if status == 401 and "invalid token" in body:
        return "application_reached_invalid_token"
    if status == 404:
        return "endpoint_not_found_or_unsupported"
    if status in {400, 403} and "model" in body:
        return "model_or_permission_rejected"
    if status is not None and 200 <= status < 300:
        return "success"
    if status is not None:
        return "http_error"
    return "transport_error"


def summarize_response(
    *,
    name: str,
    method: str,
    path: str,
    authenticated: bool,
    elapsed_ms: float,
    status: int | None,
    headers: dict[str, str],
    body: bytes,
    error_type: str | None = None,
    error_preview: str | None = None,
) -> dict[str, object]:
    safe_headers = {
        key.lower(): value
        for key, value in headers.items()
        if key.lower() in SAFE_RESPONSE_HEADERS
    }
    body_preview = body.decode("utf-8", "replace")[:500]
    result: dict[str, object] = {
        "name": name,
        "method": method,
        "path": path,
        "authenticated": authenticated,
        "latency_ms": round(float(elapsed_ms), 1),
        "response_headers": safe_headers,
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "body_preview": body_preview,
        "classification": classify_response(status, body_preview, safe_headers),
    }
    if status is not None:
        result["status"] = int(status)
    if error_type is not None:
        result["error_type"] = error_type
    if error_preview is not None:
        result["error_preview"] = error_preview[:500]
    return result


def run_case(
    *,
    base_url: str,
    api_key: str,
    name: str,
    method: str,
    path: str,
    payload: dict[str, object] | None,
    authenticated: bool,
    user_agent: str = DEFAULT_USER_AGENT,
    timeout: float = 60.0,
    no_proxy: bool = True,
) -> dict[str, object]:
    body = json.dumps(payload).encode() if payload is not None else None
    headers = build_headers(
        api_key=api_key,
        authenticated=authenticated,
        has_body=body is not None,
        user_agent=user_agent,
    )
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=body,
        headers=headers,
        method=method,
    )
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({} if no_proxy else None)
    )
    started = time.perf_counter()
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(2048)
            return summarize_response(
                name=name,
                method=method,
                path=path,
                authenticated=authenticated,
                elapsed_ms=(time.perf_counter() - started) * 1000,
                status=response.status,
                headers=dict(response.headers.items()),
                body=raw,
            )
    except urllib.error.HTTPError as exc:
        raw = exc.read(2048)
        return summarize_response(
            name=name,
            method=method,
            path=path,
            authenticated=authenticated,
            elapsed_ms=(time.perf_counter() - started) * 1000,
            status=exc.code,
            headers=dict(exc.headers.items()),
            body=raw,
        )
    except Exception as exc:
        return summarize_response(
            name=name,
            method=method,
            path=path,
            authenticated=authenticated,
            elapsed_ms=(time.perf_counter() - started) * 1000,
            status=None,
            headers={},
            body=b"",
            error_type=type(exc).__name__,
            error_preview=repr(exc),
        )


def build_report(
    *,
    base_url: str,
    api_key: str,
    tests: list[dict[str, object]],
    proxy: dict[str, str | None],
) -> dict[str, object]:
    origin = urlsplit(base_url)
    return {
        "artifact_type": "labforge_openai_compatibility_diagnostic",
        "base_url_origin": f"{origin.scheme}://{origin.netloc}",
        "base_url_path": origin.path,
        "credential_fingerprint": "sha256:" + hashlib.sha256(api_key.encode()).hexdigest()[:16],
        "default_user_agent": DEFAULT_USER_AGENT,
        "proxy": proxy,
        "tests": tests,
    }


def default_cases(model: str) -> list[tuple[str, str, str, dict[str, object] | None, bool]]:
    return [
        ("models_unauthenticated_openai_ua", "GET", "/models", None, False),
        ("models_authenticated_openai_ua", "GET", "/models", None, True),
        (
            "chat_model_openai_ua",
            "POST",
            "/chat/completions",
            {
                "model": model,
                "messages": [{"role": "user", "content": "Reply with OK."}],
                "max_tokens": 8,
                "temperature": 0,
            },
            True,
        ),
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL", ""))
    parser.add_argument("--api-key", default=os.environ.get("OPENAI_API_KEY", ""))
    parser.add_argument("--model", default=os.environ.get("GPT55_MODEL", "gpt-5.5"))
    parser.add_argument(
        "--output",
        default=default_output_path(),
    )
    parser.add_argument("--allow-proxy", action="store_true")
    args = parser.parse_args()

    if not args.base_url:
        raise SystemExit("OPENAI_BASE_URL is required")
    if not args.api_key:
        raise SystemExit("OPENAI_API_KEY is required")

    tests = [
        run_case(
            base_url=args.base_url,
            api_key=args.api_key,
            name=name,
            method=method,
            path=path,
            payload=payload,
            authenticated=authenticated,
            no_proxy=not args.allow_proxy,
        )
        for name, method, path, payload, authenticated in default_cases(args.model)
    ]
    report = build_report(
        base_url=args.base_url,
        api_key=args.api_key,
        tests=tests,
        proxy={name: os.environ.get(name) for name in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY")},
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    print("artifact=" + str(output))
    print("artifact_sha256=" + hashlib.sha256(output.read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
