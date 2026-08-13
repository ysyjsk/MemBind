"""Mock-only request, retry, failure, redaction, and provenance contracts."""

from __future__ import annotations

import asyncio
import hashlib
import json
import socket
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase, mock

import httpx


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from evaluation.backends.base import BackendStatus  # noqa: E402
from evaluation.backends.openai_compatible import (  # noqa: E402
    OpenAICompatibleJudgeBackend,
    Qwen3JudgeBackend,
    canonical_json_sha256,
)
from evaluation.provenance import (  # noqa: E402
    build_judge_upstream_manifest,
    validate_judge_upstream_manifest,
    write_judge_upstream_manifest,
)


class FakeStatusError(RuntimeError):
    def __init__(self, status_code: int, message: str = "secret error") -> None:
        super().__init__(message)
        self.status_code = status_code


class FakeCompletions:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.requests.append(dict(kwargs))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=outcome))]
        )


class FakeClient:
    def __init__(self, outcomes: list[object]) -> None:
        self.max_retries = 0
        self.chat = SimpleNamespace(completions=FakeCompletions(outcomes))


async def no_sleep(_delay: float) -> None:
    return None


class Qwen3JudgeBackendTests(IsolatedAsyncioTestCase):
    async def test_fake_client_path_never_opens_a_real_socket(self) -> None:
        client = FakeClient(["YES"])
        with mock.patch.object(
            socket.socket,
            "connect",
            side_effect=AssertionError("real network forbidden in Judge offline tests"),
        ):
            result = await Qwen3JudgeBackend(
                base_url="http://judge.invalid/v1",
                api_key="PRIVATE",
                client=client,
            ).judge("official prompt")
        self.assertEqual(result.status, BackendStatus.SUCCESS)
        self.assertEqual(len(client.chat.completions.requests), 1)

    async def test_request_is_exact_official_config_and_client_side_thinking_disabled(self) -> None:
        client = FakeClient(["YES"])
        backend = Qwen3JudgeBackend(
            base_url="http://judge.invalid/v1/",
            api_key="PRIVATE-JUDGE-KEY",
            client=client,
            max_attempts=1,
            thinking_control="client_request",
        )
        result = await backend.judge("official prompt")
        self.assertEqual(result.status, BackendStatus.SUCCESS)
        self.assertEqual(result.raw_output, "YES")
        self.assertEqual(
            client.chat.completions.requests,
            [{
                "model": "qwen3-32b-fp8",
                "messages": [{"role": "user", "content": "official prompt"}],
                "temperature": 0,
                "max_tokens": 10,
                "n": 1,
                "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
            }],
        )
        self.assertNotIn("PRIVATE-JUDGE-KEY", repr(result))
        self.assertNotIn("PRIVATE-JUDGE-KEY", repr(backend.public_config))
        self.assertEqual(backend.public_config["effective_enable_thinking"], False)

    async def test_server_side_thinking_freeze_does_not_send_second_override(self) -> None:
        client = FakeClient(["NO"])
        backend = Qwen3JudgeBackend(
            base_url="http://judge.invalid/v1",
            api_key="PRIVATE",
            client=client,
            max_attempts=1,
            thinking_control="server_side",
        )
        await backend.judge("prompt")
        request = client.chat.completions.requests[0]
        self.assertNotIn("extra_body", request)
        self.assertEqual(backend.public_config["thinking_control"], "server_side")
        self.assertFalse(backend.public_config["effective_enable_thinking"])

    async def test_only_infrastructure_failures_retry_and_bookkeeping_is_exact(self) -> None:
        for first_error in (
            asyncio.TimeoutError("private timeout"),
            ConnectionError("private connection"),
            httpx.ConnectError("private reset"),
            httpx.ReadTimeout("private read timeout"),
            FakeStatusError(429),
            FakeStatusError(500),
            FakeStatusError(599),
        ):
            with self.subTest(error=type(first_error).__name__, status=getattr(first_error, "status_code", None)):
                client = FakeClient([first_error, "YES"])
                backend = Qwen3JudgeBackend(
                    base_url="http://judge.invalid/v1",
                    api_key="PRIVATE",
                    client=client,
                    max_attempts=2,
                    sleep=no_sleep,
                )
                result = await backend.judge("prompt")
                self.assertEqual(result.status, BackendStatus.SUCCESS)
                self.assertEqual(result.retry_count, 1)
                self.assertEqual(len(client.chat.completions.requests), 2)

        for terminal in ("NO", "maybe"):
            client = FakeClient([terminal, "YES"])
            result = await Qwen3JudgeBackend(
                base_url="http://judge.invalid/v1",
                api_key="PRIVATE",
                client=client,
                max_attempts=2,
                sleep=no_sleep,
            ).judge("prompt")
            self.assertEqual(result.raw_output, terminal)
            self.assertEqual(result.retry_count, 0)
            self.assertEqual(len(client.chat.completions.requests), 1)

    async def test_injected_client_with_hidden_retries_fails_closed(self) -> None:
        client = FakeClient(["YES"])
        client.max_retries = 2
        with self.assertRaises(ValueError):
            Qwen3JudgeBackend(
                base_url="http://judge.invalid/v1",
                api_key="PRIVATE",
                client=client,
            )

    async def test_exhausted_or_nonretryable_failure_is_service_error_without_false_label_or_secret(self) -> None:
        for outcomes, expected_retries in (
            ([FakeStatusError(500, "API key PRIVATE") for _ in range(2)], 1),
            ([FakeStatusError(400, "Authorization Bearer PRIVATE")], 0),
        ):
            client = FakeClient(outcomes)
            result = await Qwen3JudgeBackend(
                base_url="http://judge.invalid/v1",
                api_key="PRIVATE",
                client=client,
                max_attempts=2,
                sleep=no_sleep,
            ).judge("private question")
            self.assertEqual(result.status, BackendStatus.SERVICE_ERROR)
            self.assertIsNone(result.raw_output)
            self.assertEqual(result.retry_count, expected_retries)
            rendered = repr(result)
            self.assertNotIn("PRIVATE", rendered)
            self.assertNotIn("password", rendered)
            self.assertIsNotNone(result.error_class)

    async def test_endpoint_userinfo_is_rejected_without_echoing_secret(self) -> None:
        with self.assertRaises(Exception) as raised:
            Qwen3JudgeBackend(
                base_url="http://user:PRIVATE-PASSWORD@judge.invalid/v1",
                api_key="PRIVATE-KEY",
                client=FakeClient(["YES"]),
            )
        rendered = str(raised.exception)
        self.assertNotIn("PRIVATE-PASSWORD", rendered)
        self.assertNotIn("PRIVATE-KEY", rendered)

    async def test_generic_backend_is_model_configurable_without_qwen_prompt_logic(self) -> None:
        client = FakeClient(["YES"])
        backend = OpenAICompatibleJudgeBackend(
            model="other-eval-model",
            base_url="http://judge.invalid/v1",
            api_key="PRIVATE",
            client=client,
            temperature=0,
            max_tokens=10,
            n=1,
            enable_thinking=None,
            max_attempts=1,
        )
        await backend.judge("benchmark-owned prompt")
        self.assertEqual(client.chat.completions.requests[0]["model"], "other-eval-model")
        self.assertNotIn("longmemeval", repr(vars(backend)).lower())

    async def test_mock_transport_proves_chat_completions_wire_path_and_zero_sdk_retry(self) -> None:
        observed: list[dict[str, object]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            observed.append(
                {
                    "method": request.method,
                    "path": request.url.path,
                    "body": json.loads(request.content),
                }
            )
            return httpx.Response(
                200,
                json={
                    "id": "offline-fixture",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "qwen3-32b-fp8",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "YES"},
                            "finish_reason": "stop",
                        }
                    ],
                },
            )

        backend = Qwen3JudgeBackend(
            base_url="http://judge.invalid/v1",
            api_key="PRIVATE",
            transport=httpx.MockTransport(handler),
            max_attempts=1,
        )
        try:
            result = await backend.judge("official prompt")
        finally:
            await backend.aclose()

        self.assertEqual(result.status, BackendStatus.SUCCESS)
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0]["method"], "POST")
        self.assertEqual(observed[0]["path"], "/v1/chat/completions")
        body = observed[0]["body"]
        self.assertEqual(body["model"], "qwen3-32b-fp8")
        self.assertEqual(body["messages"], [{"role": "user", "content": "official prompt"}])
        self.assertEqual(body["temperature"], 0)
        self.assertEqual(body["max_tokens"], 10)
        self.assertEqual(body["n"], 1)
        self.assertEqual(
            body["chat_template_kwargs"],
            {"enable_thinking": False},
        )
        self.assertEqual(backend._client.max_retries, 0)
        self.assertFalse(backend._http_client.follow_redirects)

    async def test_public_config_is_a_defensive_copy_bound_to_config_hash(self) -> None:
        backend = Qwen3JudgeBackend(
            base_url="http://judge.invalid/v1",
            api_key="PRIVATE",
            client=FakeClient(["YES"]),
        )
        observed = backend.public_config
        self.assertEqual(backend.config_hash, canonical_json_sha256(observed))
        observed["max_tokens"] = 999
        self.assertEqual(backend.public_config["max_tokens"], 10)
        self.assertEqual(backend.config_hash, canonical_json_sha256(backend.public_config))


class JudgeProvenanceTests(TestCase):
    def test_manifest_binds_pinned_upstreams_local_adapter_and_sanitized_config(self) -> None:
        manifest = build_judge_upstream_manifest(ROOT)
        longmem = manifest["upstreams"]["longmemeval"]
        timem = manifest["upstreams"]["timem"]
        self.assertEqual(longmem["commit_sha"], "9e0b455f4ef0e2ab8f2e582289761153549043fc")
        self.assertEqual(longmem["source_path"], "src/evaluation/evaluate_qa.py")
        self.assertEqual(longmem["source_sha256"], "ecce9c4c79dc89d99534ac17b383a5cbb5b9f0c69ee98adaf0684742e3d95251")
        self.assertEqual(longmem["source_git_blob_sha"], "4732f3772b04a2b9069121ade304e6320494abc2")
        self.assertEqual(timem["commit_sha"], "6d279a5f5d40ee229e1995df15c182cb2062c71c")
        self.assertEqual(timem["source_sha256"], "11cf1a281fd217fc65ff9681ff64f7d55f61c5f7cbec3136f5a8a928de99233c")
        self.assertEqual(timem["source_git_blob_sha"], "5cf4cd4c45a0c8cf1ba18dd50b4346516e15bfa9")
        self.assertEqual(longmem["vendor_scope_ast_sha256"], "61836bc870cde12ca14cfae10d91f508eec3de6ed1f0d689fde37937083aa2a9")
        self.assertEqual(longmem["license_notice_sha256"], "d3c4b9aa54759df6ded337978a6f3b55b75615e5e4525c3b82d7e2627d4b9732")
        self.assertEqual(manifest["provenance_roles"]["rubric_semantics"], "LongMemEval official")
        self.assertEqual(manifest["provenance_roles"]["adapter_pattern"], "TiMEM engineering reference")
        self.assertEqual(manifest["provenance_roles"]["judge_backend"], "MemBind local Qwen3 adapter")
        self.assertRegex(longmem["local_vendor_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(
            manifest["local"]["implementation_files"]["adapter"]["sha256"],
            r"^[0-9a-f]{64}$",
        )
        self.assertRegex(
            manifest["local"]["offline_default_request_policy_hash"],
            r"^[0-9a-f]{64}$",
        )
        rendered = json.dumps(manifest, sort_keys=True)
        self.assertNotIn("api_key", rendered.lower())
        self.assertNotIn("authorization", rendered.lower())
        self.assertNotIn(".env", rendered.lower())

    def test_manifest_writer_is_canonical_exclusive_and_mutation_fails_closed(self) -> None:
        manifest = build_judge_upstream_manifest(ROOT)
        with self.assertRaises(TypeError):
            validate_judge_upstream_manifest(manifest)
        self.assertEqual(validate_judge_upstream_manifest(manifest, ROOT), manifest)
        self.assertEqual(
            manifest["upstreams"]["longmemeval"]["vendor_scope"],
            "get_anscheck_prompt only",
        )
        self.assertFalse(
            manifest["upstreams"]["longmemeval"]["full_upstream_file_vendored"]
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "judge_manifest.json"
            written = write_judge_upstream_manifest(path, ROOT)
            raw = path.read_bytes()
            self.assertEqual(json.loads(raw.decode("ascii")), written)
            self.assertTrue(raw.endswith(b"\n"))
            with self.assertRaises(FileExistsError):
                write_judge_upstream_manifest(path, ROOT)

        mutations = (
            ("longmemeval commit", lambda value: value["upstreams"]["longmemeval"].__setitem__("commit_sha", "main")),
            ("longmemeval source path", lambda value: value["upstreams"]["longmemeval"].__setitem__("source_path", "wrong.py")),
            ("local vendor digest", lambda value: value["upstreams"]["longmemeval"].__setitem__("local_vendor_sha256", "0" * 64)),
            ("TiMEM source path", lambda value: value["upstreams"]["timem"].__setitem__("source_path", "wrong.py")),
            ("local adapter digest", lambda value: value["local"]["implementation_files"]["adapter"].__setitem__("sha256", "0" * 64)),
            ("default policy", lambda value: value["local"]["offline_default_request_policy"].__setitem__("max_tokens", 999)),
        )
        for name, mutate in mutations:
            with self.subTest(name=name):
                mutated = json.loads(json.dumps(manifest))
                mutate(mutated)
                mutated.pop("payload_sha256")
                mutated["payload_sha256"] = canonical_json_sha256(mutated)
                with self.assertRaises(ValueError):
                    validate_judge_upstream_manifest(mutated, ROOT)

    def test_vendor_scope_rejects_extra_top_level_code(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "validation"
            for relative in (
                "src/evaluation/vendor/longmemeval_evaluate_qa.py",
                "src/evaluation/benchmarks/longmemeval.py",
                "src/evaluation/backends/openai_compatible.py",
                "src/evaluation/backends/base.py",
                "src/evaluation/registry.py",
                "src/evaluation/schemas.py",
                "src/evaluation/vendor/LONGMEMEVAL_LICENSE",
            ):
                source = ROOT / relative
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read_bytes())
            vendor = root / "src/evaluation/vendor/longmemeval_evaluate_qa.py"
            vendor.write_text(
                vendor.read_text(encoding="utf-8") + "\nEXTRA_EXECUTABLE = True\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "vendored rubric module scope"):
                build_judge_upstream_manifest(root)
