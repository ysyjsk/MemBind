"""TDD contract for a bounded, isolated OpenAI-compatible Chat judge.

The subject module intentionally does not exist at the RED checkpoint.  These
tests use synthetic config and an injected transport, so they never read the
operator's credential files or perform a network request.
"""

from __future__ import annotations

import json
import os
import tempfile
import urllib.request
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from gpt55_temporary.simple_judge import config_chat_judge as judge


class FakeTransport:
    """Record one bounded request and return a deterministic fake response."""

    def __init__(self, response: judge.TransportResponse | BaseException):
        self.response = response
        self.calls: list[dict[str, object]] = []

    def post_json(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


def _write_codex_config(
    path: Path,
    *,
    model: str | None = None,
    provider_token: str | None = None,
) -> None:
    model_line = f'model = "{model}"\n' if model is not None else ""
    token_line = (
        f'experimental_bearer_token = "{provider_token}"\n'
        if provider_token is not None
        else ""
    )
    path.write_text(
        model_line
        + 'model_provider = "test-relay"\n'
        + '\n[model_providers.test-relay]\n'
        + 'name = "Synthetic Relay"\n'
        + 'base_url = "https://relay.example.test/v1/"\n'
        + 'wire_api = "chat"\n'
        + 'requires_openai_auth = true\n'
        + token_line,
        encoding="utf-8",
    )


def _config(tmp: Path, *, key: str = "unit-test-secret") -> judge.RelayConfig:
    config_path = tmp / "config.toml"
    _write_codex_config(config_path)
    return judge.load_relay_config(
        config_path=config_path,
        environ={"OPENAI_API_KEY": key},
    )


def _success_response(content: str = "PASS") -> judge.TransportResponse:
    body = {
        "id": "chatcmpl-unit-test",
        "object": "chat.completion",
        "model": "gpt-5.4-mini",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 11,
            "completion_tokens": 3,
            "total_tokens": 14,
        },
    }
    return judge.TransportResponse(
        status_code=200,
        headers={
            "x-request-id": "request-unit-test",
            "openai-processing-ms": "12.5",
            "authorization": "must-not-be-persisted",
        },
        body=json.dumps(body).encode("utf-8"),
    )


class ConfigParsingTests(TestCase):
    """Resolve the active Codex relay without copying credentials to disk."""

    def test_loads_active_chat_provider_and_defaults_to_gpt_5_4_mini(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            config = _config(tmp)

        self.assertEqual(config.base_url, "https://relay.example.test/v1")
        self.assertEqual(config.model, "gpt-5.4-mini")
        self.assertEqual(config.api_key, "unit-test-secret")
        self.assertEqual(config.wire_api, "chat")
        self.assertNotIn(config.api_key, repr(config))

    def test_active_codex_model_is_ignored_but_explicit_adapter_model_wins(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            config_path = tmp / "config.toml"
            _write_codex_config(config_path, model="gpt-5.6-terra")
            config = judge.load_relay_config(
                config_path=config_path,
                environ={"OPENAI_API_KEY": "unit-test-secret"},
            )
            self.assertEqual(config.model, "gpt-5.4-mini")

            config = judge.load_relay_config(
                config_path=config_path,
                environ={"OPENAI_API_KEY": "unit-test-secret"},
                model="gpt-5.5",
            )
            self.assertEqual(config.model, "gpt-5.5")

    def test_non_chat_provider_requires_explicit_user_authorized_override(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            config_path = tmp / "config.toml"
            _write_codex_config(
                config_path,
                model="gpt-5.6-terra",
                provider_token="provider-only-secret",
            )

            config_path.write_text(
                config_path.read_text(encoding="utf-8").replace(
                    'wire_api = "chat"', 'wire_api = "responses"'
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "chat"):
                judge.load_relay_config(
                    config_path=config_path,
                    environ={},
                )

            config = judge.load_relay_config(
                config_path=config_path,
                environ={},
                allow_config_wire_override=True,
            )
            self.assertEqual(config.api_key, "provider-only-secret")
            self.assertEqual(config.model, "gpt-5.4-mini")
            self.assertEqual(config.wire_api, "chat")
            self.assertEqual(config.config_declared_wire_api, "responses")

    def test_missing_credential_fails_before_transport_construction(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            config_path = Path(raw_tmp) / "config.toml"
            _write_codex_config(config_path)
            with self.assertRaisesRegex(ValueError, "OPENAI_API_KEY"):
                judge.load_relay_config(config_path=config_path, environ={})


class ChatWireContractTests(TestCase):
    """Freeze the relay path and the exact no-system-message payload."""

    def test_builds_exact_chat_completions_url_and_single_user_payload(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            config = _config(Path(raw_tmp))
            request = judge.JudgeRequest(
                attempt_id="judge-wire-001",
                mode="text",
                prompt="Judge this answer exactly as supplied.",
                max_tokens=64,
            )

            self.assertEqual(
                judge.chat_completions_url(config.base_url),
                "https://relay.example.test/v1/chat/completions",
            )
            payload = judge.build_chat_payload(config=config, request=request)

        self.assertEqual(
            payload,
            {
                "model": "gpt-5.4-mini",
                "messages": [
                    {"role": "user", "content": "Judge this answer exactly as supplied."}
                ],
                "max_tokens": 64,
            },
        )
        self.assertNotIn("system", repr(payload).casefold())
        self.assertNotIn("seed", payload)
        self.assertNotIn("extra_body", payload)
        self.assertNotIn("chat_template_kwargs", payload)

    def test_text_and_code_modes_preserve_content_without_wrapping(self):
        content = 'def answer():\n    return "中文"\n'
        with tempfile.TemporaryDirectory() as raw_tmp:
            config = _config(Path(raw_tmp))
            for mode in ("text", "code"):
                with self.subTest(mode=mode):
                    request = judge.JudgeRequest(
                        attempt_id=f"judge-{mode}-001",
                        mode=mode,
                        prompt=content,
                    )
                    payload = judge.build_chat_payload(config=config, request=request)
                    self.assertEqual(payload["messages"], [{"role": "user", "content": content}])


class ResponseAndSanitizationTests(TestCase):
    """Parse useful response metadata without retaining unsafe material."""

    def test_parses_first_choice_usage_and_allowlisted_transport_metadata(self):
        parsed = judge.parse_chat_response(_success_response())

        self.assertEqual(parsed.content, "PASS")
        self.assertEqual(parsed.model, "gpt-5.4-mini")
        self.assertEqual(parsed.finish_reason, "stop")
        self.assertEqual(parsed.prompt_tokens, 11)
        self.assertEqual(parsed.completion_tokens, 3)
        self.assertEqual(parsed.total_tokens, 14)
        self.assertEqual(parsed.request_id, "request-unit-test")
        self.assertEqual(parsed.provider_processing_ms, 12.5)
        self.assertNotIn("authorization", repr(parsed).casefold())

    def test_rejects_success_response_without_assistant_content(self):
        invalid = judge.TransportResponse(
            status_code=200,
            headers={"x-request-id": "request-invalid-test"},
            body=b'{"model":"gpt-5.4-mini","choices":[]}',
        )
        with self.assertRaisesRegex(judge.ProtocolError, "choices"):
            judge.parse_chat_response(invalid)

    def test_recursive_sanitizer_removes_secret_bearer_and_url_query(self):
        secret = "unit-test-secret"
        value = {
            "message": f"Authorization: Bearer {secret}",
            "url": f"https://relay.example.test/v1?api_key={secret}",
            "nested": [f"failed with {secret}"],
        }

        sanitized = judge.sanitize_for_artifact(value, secrets=(secret,))
        serialized = json.dumps(sanitized, sort_keys=True)

        self.assertNotIn(secret, serialized)
        self.assertNotIn("Bearer ", serialized)
        self.assertNotIn("api_key=", serialized)
        self.assertIn("<redacted>", serialized)


class BoundedRunnerTests(TestCase):
    """Keep retries disabled and preserve interruption-safe segmented evidence."""

    def test_success_uses_one_request_and_writes_atomic_segmented_artifacts(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            config = _config(tmp)
            transport = FakeTransport(_success_response())
            artifact_root = tmp / "artifacts"
            request = judge.JudgeRequest(
                attempt_id="judge-success-001",
                mode="text",
                prompt="Return PASS or FAIL.",
                max_tokens=32,
            )
            with patch.object(judge.os, "replace", wraps=os.replace) as replace:
                result = judge.run_judge(
                    config=config,
                    request=request,
                    transport=transport,
                    artifact_root=artifact_root,
                )

            run_dir = artifact_root / request.attempt_id
            expected = [
                "00_manifest.json",
                "01_request.json",
                "02_transport.json",
                "03_response.json",
                "04_summary.json",
            ]
            self.assertEqual(result.status, "success")
            self.assertEqual(len(transport.calls), 1)
            self.assertEqual(sorted(path.name for path in run_dir.iterdir()), expected)
            self.assertGreaterEqual(replace.call_count, len(expected))
            self.assertFalse(list(run_dir.glob("*.tmp")))
            for name in expected:
                json.loads((run_dir / name).read_text(encoding="utf-8"))

            call = transport.calls[0]
            self.assertEqual(
                call["url"],
                "https://relay.example.test/v1/chat/completions",
            )
            self.assertEqual(call["headers"]["User-Agent"], "OpenAI/Python 1.0.0")
            self.assertEqual(call["headers"]["Authorization"], "Bearer unit-test-secret")
            self.assertEqual(call["max_retries"], 0)
            transport_artifact = json.loads(
                (run_dir / "02_transport.json").read_text(encoding="utf-8")
            )
            self.assertEqual(transport_artifact["attempt_count"], 1)
            self.assertGreaterEqual(transport_artifact["client_observed_latency_ms"], 0)
            persisted = "\n".join(
                path.read_text(encoding="utf-8")
                for path in run_dir.iterdir()
                if path.is_file()
            )
            self.assertNotIn(request.prompt, persisted)
            self.assertNotIn("PASS", persisted)

    def test_wrong_model_or_incomplete_finish_reason_is_not_success(self):
        cases = (("another-model", "stop"), ("gpt-5.4-mini", "length"))
        for index, (returned_model, finish_reason) in enumerate(cases):
            with self.subTest(returned_model=returned_model, finish_reason=finish_reason):
                with tempfile.TemporaryDirectory() as raw_tmp:
                    tmp = Path(raw_tmp)
                    config = _config(tmp)
                    response = _success_response()
                    body = json.loads(response.body)
                    body["model"] = returned_model
                    body["choices"][0]["finish_reason"] = finish_reason
                    transport = FakeTransport(
                        judge.TransportResponse(
                            status_code=200,
                            headers=response.headers,
                            body=json.dumps(body).encode("utf-8"),
                        )
                    )
                    result = judge.run_judge(
                        config=config,
                        request=judge.JudgeRequest(
                            attempt_id=f"judge-incomplete-{index}",
                            mode="text",
                            prompt="bounded",
                        ),
                        transport=transport,
                        artifact_root=tmp / "artifacts",
                    )

                self.assertEqual(result.status, "failed")
                self.assertEqual(result.error_class, "protocol_error")
                self.assertEqual(len(transport.calls), 1)

    def test_attempt_directory_is_exclusively_created_before_transport(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            config = _config(tmp)
            root = tmp / "artifacts"
            occupied = root / "judge-exclusive-001"
            occupied.mkdir(parents=True)
            transport = FakeTransport(_success_response())

            with self.assertRaises(FileExistsError):
                judge.run_judge(
                    config=config,
                    request=judge.JudgeRequest(
                        attempt_id="judge-exclusive-001",
                        mode="text",
                        prompt="bounded",
                    ),
                    transport=transport,
                    artifact_root=root,
                )

            self.assertEqual(transport.calls, [])

    def test_timeout_is_not_retried_and_all_artifacts_are_secret_free(self):
        secret = "unit-test-secret"
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            config = _config(tmp, key=secret)
            transport = FakeTransport(
                TimeoutError(
                    f"Bearer {secret} timed out at "
                    f"https://relay.example.test/v1?api_key={secret}"
                )
            )
            artifact_root = tmp / "artifacts"
            request = judge.JudgeRequest(
                attempt_id="judge-timeout-001",
                mode="code",
                prompt="print('bounded')",
            )

            result = judge.run_judge(
                config=config,
                request=request,
                transport=transport,
                artifact_root=artifact_root,
            )

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.error_class, "timeout")
            self.assertEqual(len(transport.calls), 1)
            persisted = "\n".join(
                path.read_text(encoding="utf-8")
                for path in (artifact_root / request.attempt_id).iterdir()
                if path.is_file()
            )
            self.assertNotIn(secret, persisted)
            self.assertNotIn("Bearer ", persisted)
            self.assertNotIn("api_key=", persisted)
            summary = json.loads(
                (artifact_root / request.attempt_id / "04_summary.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(summary["attempt_count"], 1)
            self.assertEqual(summary["status"], "failed")


class AttemptPathBoundaryTests(TestCase):
    """Reject path traversal before any attempt directory is created."""

    def test_accepts_bounded_identifier_and_rejects_escape_forms(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp) / "artifacts"
            accepted = judge.resolve_attempt_dir(root, "judge-20260811_001")
            self.assertEqual(accepted, root / "judge-20260811_001")

            for attempt_id in ("", ".", "../escape", "a/b", "/tmp/escape", "a" * 129):
                with self.subTest(attempt_id=attempt_id):
                    with self.assertRaises(ValueError):
                        judge.resolve_attempt_dir(root, attempt_id)

            self.assertFalse(root.exists(), "validation alone must not create artifact paths")

    def test_rejects_symlink_artifact_root_and_default_stays_inside_lane(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            outside = tmp / "outside"
            outside.mkdir()
            root = tmp / "artifacts-link"
            root.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                judge.prepare_attempt_dir(root, "judge-symlink-001")

        lane = Path(judge.__file__).resolve().parents[1]
        self.assertTrue(judge.DEFAULT_ARTIFACT_ROOT.is_relative_to(lane))


class UrllibTransportSafetyTests(TestCase):
    """The standard transport must not redirect or read an unbounded body."""

    def test_redirect_handler_refuses_every_redirect(self):
        handler = judge.NoRedirectHandler()
        self.assertIsNone(
            handler.redirect_request(
                None,
                None,
                302,
                "Found",
                {},
                "https://other-origin.example/steal",
            )
        )

    def test_default_transport_explicitly_disables_environment_proxy(self):
        with patch.object(judge.urllib.request, "build_opener") as build_opener:
            judge.UrllibChatTransport()

        handlers = build_opener.call_args.args
        proxy_handlers = [
            handler for handler in handlers if isinstance(handler, urllib.request.ProxyHandler)
        ]
        self.assertEqual(len(proxy_handlers), 1)
        self.assertEqual(proxy_handlers[0].proxies, {})
        self.assertTrue(any(isinstance(handler, judge.NoRedirectHandler) for handler in handlers))

    def test_response_size_limit_is_enforced(self):
        class OversizedResponse:
            status = 200
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, size):
                return b"x" * size

        class FakeOpener:
            def open(self, request, timeout):
                return OversizedResponse()

        transport = judge.UrllibChatTransport(
            opener=FakeOpener(),
            max_response_bytes=32,
        )
        with self.assertRaisesRegex(judge.ProtocolError, "size"):
            transport.post_json(
                url="https://relay.example.test/chat/completions",
                headers={"Authorization": "Bearer test"},
                payload={"model": "gpt-5.4-mini", "messages": []},
                timeout_s=1.0,
                max_retries=0,
            )


class OpenAISdkTransportTests(TestCase):
    """The SDK transport keeps one create call and returns a normal envelope."""

    def test_sdk_transport_uses_exact_payload_once_without_retry(self):
        parsed_body = json.loads(_success_response().body)

        class Parsed:
            def model_dump_json(self):
                return json.dumps(parsed_body)

        class RawResponse:
            status_code = 200
            headers = {"x-request-id": "sdk-request-test"}

            def parse(self):
                return Parsed()

        class Endpoint:
            def __init__(self):
                self.calls = []

            def create(self, **kwargs):
                self.calls.append(kwargs)
                return RawResponse()

        class Completions:
            def __init__(self, endpoint):
                self.with_raw_response = endpoint

        class Chat:
            def __init__(self, endpoint):
                self.completions = Completions(endpoint)

        class Client:
            def __init__(self, endpoint):
                self.chat = Chat(endpoint)

        endpoint = Endpoint()
        transport = judge.OpenAISdkChatTransport(
            base_url="https://relay.example.test/v1",
            api_key="unit-test-secret",
            timeout_s=10,
            client=Client(endpoint),
        )
        payload = {
            "model": "gpt-5.4-mini",
            "messages": [{"role": "user", "content": "bounded"}],
            "max_tokens": 32,
        }
        response = transport.post_json(
            url="https://relay.example.test/v1/chat/completions",
            headers={
                "Authorization": "Bearer unit-test-secret",
                "User-Agent": "OpenAI/Python 1.0.0",
            },
            payload=payload,
            timeout_s=10,
            max_retries=0,
        )

        self.assertEqual(endpoint.calls, [payload])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.body), parsed_body)
        self.assertEqual(transport.proxy_policy, "direct_openai_sdk")
