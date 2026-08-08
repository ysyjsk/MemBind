import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import labforge_gateway_probe  # noqa: E402


class LabForgeGatewayProbeTests(TestCase):
    def test_default_output_is_inside_the_temporary_lane(self):
        self.assertEqual(
            labforge_gateway_probe.default_output_path(),
            "gpt55_temporary/artifacts/diagnostics/labforge_api_diagnostic_20260808.json",
        )

    def test_authenticated_headers_use_api_client_user_agent_without_leaking_key(self):
        headers = labforge_gateway_probe.build_headers(
            api_key="sk-test-secret",
            authenticated=True,
            has_body=True,
        )

        self.assertEqual(headers["User-Agent"], "OpenAI/Python 1.0.0")
        self.assertEqual(headers["Authorization"], "Bearer sk-test-secret")
        safe = labforge_gateway_probe.safe_report_header_keys(headers)
        self.assertIn("user-agent", safe)
        self.assertIn("authorization", safe)
        self.assertNotIn("sk-test-secret", repr(safe))

    def test_default_cases_use_chat_completions_not_responses(self):
        cases = labforge_gateway_probe.default_cases("gpt-5.5")
        paths = [path for _, _, path, _, _ in cases]

        self.assertIn("/models", paths)
        self.assertIn("/chat/completions", paths)
        self.assertNotIn("/responses", paths)

    def test_classifies_cloudflare_1010_before_application_layer(self):
        result = labforge_gateway_probe.summarize_response(
            name="models_default_ua",
            method="GET",
            path="/models",
            authenticated=False,
            elapsed_ms=12.3,
            status=403,
            headers={"server": "cloudflare", "cf-ray": "abc"},
            body=b"error code: 1010\n",
        )

        self.assertEqual(result["classification"], "cloudflare_waf_or_user_agent_block")
        self.assertEqual(result["status"], 403)
        self.assertNotIn("api_key", repr(result).lower())

    def test_classifies_application_layer_invalid_token_after_user_agent_fix(self):
        result = labforge_gateway_probe.summarize_response(
            name="models_openai_ua",
            method="GET",
            path="/models",
            authenticated=True,
            elapsed_ms=15.0,
            status=401,
            headers={"server": "cloudflare", "cf-ray": "abc"},
            body=b'{"error":{"message":"Invalid token","type":"new_api_error"}}',
        )

        self.assertEqual(result["classification"], "application_reached_invalid_token")

    def test_report_uses_short_credential_fingerprint_only(self):
        report = labforge_gateway_probe.build_report(
            base_url="https://api.labforge.cc/v1",
            api_key="sk-test-secret",
            tests=[{"name": "models", "status": 200}],
            proxy={"HTTP_PROXY": None, "HTTPS_PROXY": None, "NO_PROXY": None},
        )

        self.assertEqual(report["credential_fingerprint"][:7], "sha256:")
        self.assertEqual(len(report["credential_fingerprint"]), len("sha256:") + 16)
        self.assertNotIn("sk-test-secret", repr(report))
