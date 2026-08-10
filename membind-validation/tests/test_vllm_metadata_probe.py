"""Contracts for the no-completion vLLM runtime metadata probe."""

import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vllm_metadata_probe import (  # noqa: E402
    METADATA_PATHS,
    probe_vllm_metadata,
    write_vllm_metadata_probe,
)


class _Response:
    def __init__(self, payload, *, status=200, content_type="application/json"):
        self.status = status
        self.headers = {"Content-Type": content_type}
        self.body = (
            payload
            if isinstance(payload, bytes)
            else json.dumps(payload).encode("utf-8")
        )

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.body


class VLLMMetadataProbeTests(TestCase):
    def test_probe_uses_only_read_only_paths_and_whitelists_server_config(self):
        calls = []
        payloads = {
            "/version": {"version": "0.26.0"},
            "/v1/models": {
                "data": [
                    {
                        "id": "qwen3-32b-fp8",
                        "max_model_len": 40960,
                        "root": "/models/Qwen3-32B-FP8",
                    }
                ]
            },
            "/server_info?config_format=json": {
                "vllm_config": {
                    "structured_outputs_config": {
                        "backend": "auto",
                        "disable_any_whitespace": False,
                        "disable_additional_properties": False,
                        "reasoning_parser": "",
                        "enable_in_reasoning": False,
                        "future_secret": "do-not-persist",
                    },
                    "model_config": {
                        "model": "/models/Qwen3-32B-FP8",
                        "dtype": "bfloat16",
                        "max_model_len": 40960,
                        "tokenizer": "private-tokenizer-path",
                    },
                },
                "vllm_env": {"VLLM_API_KEY": "secret"},
                "system_env": {"HOME": "/secret/home"},
            },
            "/health": b"",
        }

        def open_url(request, *, timeout):
            calls.append((request.full_url, timeout, request.get_header("Authorization")))
            path = request.full_url.removeprefix("http://model:8000")
            content_type = "text/plain" if path == "/health" else "application/json"
            return _Response(payloads[path], content_type=content_type)

        result = probe_vllm_metadata(
            "http://model:8000/v1/",
            "top-secret-key",
            timeout=3.0,
            open_url=open_url,
            authorization_checker=lambda *_args, **_kwargs: None,
        )

        self.assertEqual(
            [url.removeprefix("http://model:8000") for url, _, _ in calls],
            list(METADATA_PATHS),
        )
        self.assertTrue(all(auth == "Bearer top-secret-key" for _, _, auth in calls))
        self.assertTrue(all("completions" not in url for url, _, _ in calls))
        self.assertEqual(result["version"], "0.26.0")
        self.assertEqual(result["models"][0]["max_model_len"], 40960)
        config = result["server_config"]
        self.assertEqual(config["structured_outputs_config"]["backend"], "auto")
        self.assertEqual(config["model_config"]["dtype"], "bfloat16")
        encoded = json.dumps(result, sort_keys=True)
        self.assertNotIn("top-secret-key", encoded)
        self.assertNotIn("do-not-persist", encoded)
        self.assertNotIn("VLLM_API_KEY", encoded)
        self.assertNotIn("private-tokenizer-path", encoded)
        self.assertFalse(result["secrets_persisted"])

    def test_timeouts_are_persistable_blockers_instead_of_exceptions(self):
        def time_out(_request, *, timeout):
            self.assertEqual(timeout, 0.1)
            raise TimeoutError("network timeout with no secret")

        result = probe_vllm_metadata(
            "http://model:8000/v1",
            "secret",
            timeout=0.1,
            open_url=time_out,
            authorization_checker=lambda *_args, **_kwargs: None,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(len(result["endpoint_results"]), len(METADATA_PATHS))
        self.assertTrue(
            all(item["error_type"] == "TimeoutError" for item in result["endpoint_results"])
        )

    def test_private_target_fails_closed_before_proxy_routing(self):
        def must_not_open(*_args, **_kwargs):
            self.fail("private vLLM metadata must not be sent through a proxy")

        with patch("vllm_metadata_probe.urllib.request.proxy_bypass", return_value=False):
            result = probe_vllm_metadata(
                "http://10.87.5.247:8000/v1",
                "secret",
                open_url=must_not_open,
                authorization_checker=lambda *_args, **_kwargs: None,
            )

        self.assertFalse(result["ok"])
        self.assertTrue(result["private_target"])
        self.assertFalse(result["proxy_bypass_for_target"])
        self.assertFalse(result["route_contract_ok"])
        self.assertEqual(result["blocker"], "private_target_not_in_no_proxy")
        self.assertEqual(result["endpoint_results"], [])

    def test_writer_is_exclusive_and_never_persists_credentials(self):
        def time_out(_request, *, timeout):
            self.assertEqual(timeout, 0.1)
            raise TimeoutError

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "metadata.json"

            write_vllm_metadata_probe(
                "http://model:8000/v1",
                "secret-value",
                output,
                timeout=0.1,
                open_url=time_out,
                authorization_checker=lambda *_args, **_kwargs: None,
            )
            encoded = output.read_text(encoding="ascii")

            self.assertNotIn("secret-value", encoded)
            self.assertNotIn("Authorization", encoded)
            with self.assertRaises(FileExistsError):
                write_vllm_metadata_probe(
                    "http://model:8000/v1",
                    "secret-value",
                    output,
                    timeout=0.1,
                    open_url=time_out,
                    authorization_checker=lambda *_args, **_kwargs: None,
                )


if __name__ == "__main__":
    import unittest

    unittest.main()
