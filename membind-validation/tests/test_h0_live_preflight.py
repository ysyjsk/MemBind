"""Offline contracts for the state-gated H0 construction readiness probe."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest import IsolatedAsyncioTestCase
from unittest.mock import Mock

import httpx


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import h0_artifacts  # noqa: E402
from h0_live_preflight import (  # noqa: E402
    H0ReadinessCheckpointSink,
    load_authorized_h0_runtime_identity,
    run_h0_readiness_preflight,
)
from h0_runtime import (  # noqa: E402
    H0CheckpointStore,
    canonical_json_bytes,
    canonical_json_sha256,
    sha256_file,
)
from h0_runtime import H0InfrastructureError, H0ManifestError, H0StateGateError  # noqa: E402


ARTIFACT_SET_ID = "v1_3_harness_r6"
ARTIFACT_SET_REL = f"artifacts/h0_manifest_sets/{ARTIFACT_SET_ID}"
INDEX_REL = f"{ARTIFACT_SET_REL}/resolved_manifest_index_v1_3_harness_r6.json"


class H0ReadinessPreflightTests(IsolatedAsyncioTestCase):
    def _authorization(self) -> dict[str, object]:
        return {
            "candidate_id": "Q1",
            "phase": "H0-A",
            "resolved_manifest_index_path": "artifacts/h0/index.json",
            "resolved_manifest_index_sha256": "1" * 64,
            "resolved_candidate_manifest_path": "artifacts/h0/Q1.json",
            "resolved_candidate_manifest_sha256": "2" * 64,
            "resolved_shared_base_manifest_path": "artifacts/h0/shared.json",
            "resolved_shared_base_manifest_sha256": "3" * 64,
        }

    def _identity(self) -> dict[str, object]:
        return {
            **self._authorization(),
            "artifact_set_id": ARTIFACT_SET_ID,
            "execution_harness_revision": 6,
            "base_url": "http://offline.invalid/v1/",
            "served_model_id": "qwen3-32b-fp8",
            "vllm_version": "0.26.0",
            "context_limit": 40960,
        }

    def _kwargs(self, handler, progress: list[dict[str, object]]) -> dict[str, object]:
        return {
            "state_path": ROOT / "CURRENT_STATE.json",
            "stage_attempt_id": "h0_q1_a_attempt_001",
            "candidate_id": "Q1",
            "phase": "H0-A",
            "authorization_checker": Mock(return_value=self._authorization()),
            "resolved_identity_loader": Mock(return_value=self._identity()),
            "credential_loader": Mock(
                return_value={
                    "base_url": "http://offline.invalid/v1/",
                    "api_key": "OFFLINE_SECRET",
                }
            ),
            "transport_factory": lambda: httpx.MockTransport(handler),
            "progress_sink": progress.append,
        }

    async def test_state_denial_precedes_credentials_transport_and_artifacts(self):
        order: list[str] = []

        def deny(**_kwargs):
            order.append("gate")
            raise H0StateGateError("denied")

        with self.assertRaises(H0StateGateError):
            await run_h0_readiness_preflight(
                state_path=ROOT / "CURRENT_STATE.json",
                stage_attempt_id="denied-attempt",
                candidate_id="Q1",
                phase="H0-A",
                authorization_checker=deny,
                resolved_identity_loader=Mock(side_effect=AssertionError("no manifest")),
                credential_loader=Mock(side_effect=AssertionError("no credentials")),
                transport_factory=Mock(side_effect=AssertionError("no socket")),
                progress_sink=Mock(side_effect=AssertionError("no artifact")),
            )
        self.assertEqual(order, ["gate"])

    async def test_success_probes_exact_read_only_paths_once_and_sanitizes(self):
        requests: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path == "/version":
                return httpx.Response(
                    200,
                    request=request,
                    json={"version": "0.26.0", "private": "RAW_SENTINEL"},
                )
            if request.url.path == "/v1/models":
                return httpx.Response(
                    200,
                    request=request,
                    json={"data": [{"id": "qwen3-32b-fp8"}]},
                )
            if request.url.path == "/health":
                return httpx.Response(200, request=request, content=b"")
            raise AssertionError(f"unexpected path: {request.url.path}")

        progress: list[dict[str, object]] = []
        result = await run_h0_readiness_preflight(**self._kwargs(handler, progress))

        self.assertEqual(
            [request.url.path for request in requests],
            ["/version", "/v1/models", "/health"],
        )
        self.assertTrue(all(request.method == "GET" for request in requests))
        self.assertTrue(all(request.headers["Authorization"] == "Bearer OFFLINE_SECRET" for request in requests))
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["vllm_version"], "0.26.0")
        self.assertEqual(result["served_model_id"], "qwen3-32b-fp8")
        self.assertEqual(result["context_limit"], 40960)
        self.assertTrue(result["authorized_candidate_execution_ready"])
        encoded = json.dumps({"result": result, "progress": progress}, sort_keys=True)
        self.assertNotIn("OFFLINE_SECRET", encoded)
        self.assertNotIn("RAW_SENTINEL", encoded)
        self.assertNotIn("Authorization", encoded)
        self.assertEqual(len(progress), 3)
        self.assertTrue(all(not event["candidate_advance_allowed"] for event in progress))
        for event in progress:
            self.assertEqual(event["stage_attempt_id"], "h0_q1_a_attempt_001")
            self.assertEqual(event["candidate_id"], "Q1")
            self.assertEqual(event["phase"], "H0-A")
            self.assertEqual(event["resolved_candidate_manifest_sha256"], "2" * 64)

    async def test_endpoint_must_equal_resolved_manifest_before_transport(self):
        progress: list[dict[str, object]] = []
        transport_factory = Mock(side_effect=AssertionError("transport must not exist"))
        kwargs = self._kwargs(Mock(), progress)
        kwargs["credential_loader"] = Mock(
            return_value={"base_url": "http://other.invalid/v1", "api_key": "secret"}
        )
        kwargs["transport_factory"] = transport_factory
        with self.assertRaisesRegex(H0ManifestError, "endpoint.*manifest"):
            await run_h0_readiness_preflight(**kwargs)
        transport_factory.assert_not_called()

    async def test_connection_failure_stops_after_one_request_and_reports_checkpoint(self):
        request_count = 0

        for error_type in (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError):
            with self.subTest(error_type=error_type.__name__):
                request_count = 0

                async def unavailable(request: httpx.Request) -> httpx.Response:
                    nonlocal request_count
                    request_count += 1
                    raise error_type("private transport detail", request=request)

                progress: list[dict[str, object]] = []
                with self.assertRaisesRegex(
                    H0InfrastructureError, "vllm_unreachable: stop_and_report"
                ):
                    await run_h0_readiness_preflight(**self._kwargs(unavailable, progress))

                self.assertEqual(request_count, 1)
                self.assertEqual(len(progress), 1)
                self.assertEqual(progress[0]["failure_code"], "vllm_unreachable")
                self.assertFalse(progress[0]["candidate_advance_allowed"])
                encoded = json.dumps(progress, sort_keys=True)
                self.assertNotIn("private transport detail", encoded)
                self.assertNotIn("OFFLINE_SECRET", encoded)

    async def test_version_and_model_mismatch_each_fail_closed(self):
        cases = (
            ({"version": "0.25.0"}, {"data": [{"id": "qwen3-32b-fp8"}]}, "vLLM version"),
            ({"version": "0.26.0"}, {"data": [{"id": "wrong-model"}]}, "served model"),
        )
        for version, models, message in cases:
            with self.subTest(message=message):
                async def handler(request: httpx.Request) -> httpx.Response:
                    payload = version if request.url.path == "/version" else models
                    return httpx.Response(200, request=request, json=payload)

                with self.assertRaisesRegex(H0ManifestError, message):
                    await run_h0_readiness_preflight(**self._kwargs(handler, []))

    async def test_503_is_infrastructure_but_401_and_invalid_json_are_contract_failures(self):
        async def unavailable(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, request=request, content=b"private outage")

        progress: list[dict[str, object]] = []
        with self.assertRaisesRegex(H0InfrastructureError, "vllm_unreachable"):
            await run_h0_readiness_preflight(**self._kwargs(unavailable, progress))
        self.assertEqual(progress[-1]["failure_code"], "vllm_service_unavailable")

        for status, body, failure_code in (
            (401, b"private auth body", "readiness_http_failure"),
            (200, b"not-json", "readiness_invalid_json"),
        ):
            with self.subTest(status=status):
                async def invalid(request: httpx.Request) -> httpx.Response:
                    return httpx.Response(status, request=request, content=body)

                events: list[dict[str, object]] = []
                with self.assertRaises(H0ManifestError):
                    await run_h0_readiness_preflight(**self._kwargs(invalid, events))
                self.assertEqual(events[-1]["failure_code"], failure_code)
                self.assertFalse(events[-1]["candidate_advance_allowed"])


class H0ResolvedIdentityLoaderTests(IsolatedAsyncioTestCase):
    def _write_json(self, root: Path, relative: str, value: dict[str, object]) -> str:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(canonical_json_bytes(value))
        return sha256_file(path)

    def _stage_root(self, directory: str) -> Path:
        staged = Path(directory) / "membind-validation"
        shutil.copytree(ROOT / "configs/h0", staged / "configs/h0")
        for relative in (
            *h0_artifacts.H0_EXECUTION_SOURCE_PATHS,
            "artifacts/dataset/frozen_split_v1_3.json",
            "artifacts/environment/embedding_model_fingerprint.json",
            "artifacts/environment/v3_construction_runtime_evidence_20260809.json",
        ):
            destination = staged / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, destination)
        return staged

    def _written_authorization(
        self, root: Path
    ) -> tuple[dict[str, object], dict[str, object]]:
        written = h0_artifacts.write_h0_offline_artifacts(root)
        index = written["index"]
        return written, {
            "candidate_id": "Q1",
            "phase": "H0-A",
            "resolved_manifest_index_path": written["index_path"],
            "resolved_manifest_index_sha256": written["index_sha256"],
            "resolved_candidate_manifest_path": index["resolved_manifests"]["Q1"][
                "path"
            ],
            "resolved_candidate_manifest_sha256": index["resolved_manifests"]["Q1"][
                "sha256"
            ],
            "resolved_shared_base_manifest_path": index["resolved_manifests"][
                "shared_base"
            ]["path"],
            "resolved_shared_base_manifest_sha256": index["resolved_manifests"][
                "shared_base"
            ]["sha256"],
        }

    async def test_loader_verifies_index_candidate_shared_cross_bindings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._stage_root(tmp)
            written = h0_artifacts.write_h0_offline_artifacts(root)
            index = written["index"]
            index_rel = written["index_path"]
            index_sha = written["index_sha256"]
            candidate_rel = index["resolved_manifests"]["Q1"]["path"]
            candidate_sha = index["resolved_manifests"]["Q1"]["sha256"]
            shared_rel = index["resolved_manifests"]["shared_base"]["path"]
            shared_sha = index["resolved_manifests"]["shared_base"]["sha256"]
            authorization = {
                "candidate_id": "Q1",
                "phase": "H0-A",
                "resolved_manifest_index_path": index_rel,
                "resolved_manifest_index_sha256": index_sha,
                "resolved_candidate_manifest_path": candidate_rel,
                "resolved_candidate_manifest_sha256": candidate_sha,
                "resolved_shared_base_manifest_path": shared_rel,
                "resolved_shared_base_manifest_sha256": shared_sha,
            }

            identity = load_authorized_h0_runtime_identity(authorization, root=root)

            self.assertEqual(identity["base_url"], "http://10.87.5.247:8000/v1/")
            self.assertEqual(identity["candidate_id"], "Q1")
            self.assertEqual(identity["phase"], "H0-A")
            self.assertEqual(identity["context_limit"], 40960)
            self.assertEqual(identity["artifact_set_id"], ARTIFACT_SET_ID)
            self.assertEqual(identity["execution_harness_revision"], 6)
            self.assertEqual(identity["resolved_manifest_index_sha256"], index_sha)

            for field, bad_value in (
                ("resolved_candidate_manifest_sha256", "f" * 64),
                ("resolved_shared_base_manifest_path", "../outside.json"),
            ):
                with self.subTest(field=field):
                    invalid = dict(authorization, **{field: bad_value})
                    with self.assertRaises(H0ManifestError):
                        load_authorized_h0_runtime_identity(invalid, root=root)

            candidate = json.loads((root / candidate_rel).read_text(encoding="ascii"))
            candidate["status"] = "candidate_failed"
            candidate_sha = canonical_json_sha256(candidate)
            candidate_rel = (
                f"{ARTIFACT_SET_REL}/resolved_candidates/Q1.{candidate_sha}.json"
            )
            self._write_json(root, candidate_rel, candidate)
            index["resolved_manifests"]["Q1"] = {
                "path": candidate_rel,
                "sha256": candidate_sha,
            }
            index_sha = self._write_json(root, index_rel, index)
            rehashed = dict(
                authorization,
                resolved_manifest_index_sha256=index_sha,
                resolved_candidate_manifest_path=candidate_rel,
                resolved_candidate_manifest_sha256=candidate_sha,
            )
            with self.assertRaisesRegex(H0ManifestError, "candidate"):
                load_authorized_h0_runtime_identity(rehashed, root=root)

    async def test_loader_rejects_legacy_schema_and_artifact_set_identity_drift(self):
        cases = (
            ("schema_version", "membind.h0.offline-artifacts.v1", "schema"),
            ("artifact_set_id", "different_harness_set", "artifact set"),
            ("execution_harness_revision", 2, "harness revision"),
        )
        for field, value, message in cases:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                root = self._stage_root(tmp)
                written, authorization = self._written_authorization(root)
                index = deepcopy(written["index"])
                index[field] = value
                index_sha = self._write_json(root, written["index_path"], index)
                authorization["resolved_manifest_index_sha256"] = index_sha

                with self.assertRaisesRegex(H0ManifestError, message):
                    load_authorized_h0_runtime_identity(authorization, root=root)

    async def test_loader_rejects_rehashed_index_or_candidate_outside_r3_namespace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._stage_root(tmp)
            written, authorization = self._written_authorization(root)
            original_index = root / written["index_path"]
            alternate_index = "artifacts/alternate/resolved_manifest_index.json"
            alternate_sha = self._write_json(
                root, alternate_index, deepcopy(written["index"])
            )
            alternate_authorization = {
                **authorization,
                "resolved_manifest_index_path": alternate_index,
                "resolved_manifest_index_sha256": alternate_sha,
            }

            with self.assertRaisesRegex(H0ManifestError, "index namespace"):
                load_authorized_h0_runtime_identity(
                    alternate_authorization, root=root
                )

            index = deepcopy(written["index"])
            candidate_reference = index["resolved_manifests"]["Q1"]
            candidate = json.loads(
                (root / candidate_reference["path"]).read_text(encoding="ascii")
            )
            alternate_candidate = (
                f"artifacts/alternate/Q1.{candidate_reference['sha256']}.json"
            )
            self._write_json(root, alternate_candidate, candidate)
            candidate_reference["path"] = alternate_candidate
            index_sha = self._write_json(root, written["index_path"], index)
            candidate_authorization = {
                **authorization,
                "resolved_manifest_index_sha256": index_sha,
                "resolved_candidate_manifest_path": alternate_candidate,
            }

            with self.assertRaisesRegex(H0ManifestError, "candidate namespace"):
                load_authorized_h0_runtime_identity(
                    candidate_authorization, root=root
                )

            self.assertTrue(original_index.is_file())

    async def test_loader_rejects_noncanonical_bound_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            relative = INDEX_REL
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{"protocol_version": "current-validation-v1.3"}\n', encoding="utf-8")
            authorization = {
                "resolved_manifest_index_path": relative,
                "resolved_manifest_index_sha256": sha256_file(path),
            }
            with self.assertRaisesRegex(H0ManifestError, "canonical"):
                load_authorized_h0_runtime_identity(authorization, root=root)


class H0ReadinessCheckpointSinkTests(IsolatedAsyncioTestCase):
    async def test_infrastructure_event_is_content_addressed_before_raise(self):
        async def unavailable(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("private connection detail", request=request)

        with tempfile.TemporaryDirectory() as tmp:
            store = H0CheckpointStore(
                root=Path(tmp),
                stage_attempt_id="h0_q1_a_attempt_durable",
                candidate_id="Q1",
                phase="H0-A",
            )
            sink = H0ReadinessCheckpointSink(store)
            case = H0ReadinessPreflightTests()
            kwargs = case._kwargs(unavailable, [])
            kwargs["stage_attempt_id"] = "h0_q1_a_attempt_durable"
            kwargs["progress_sink"] = sink

            with self.assertRaises(H0InfrastructureError):
                await run_h0_readiness_preflight(**kwargs)

            reopened = H0CheckpointStore.open_existing(
                Path(tmp), "h0_q1_a_attempt_durable"
            )
            self.assertEqual(reopened.index["status"], "infrastructure_interrupted")
            self.assertEqual(len(reopened.index["segments"]), 1)
            entry = reopened.index["segments"][0]
            artifact = Path(tmp) / entry["artifact_path"]
            self.assertEqual(sha256_file(artifact), entry["artifact_sha256"])
            persisted = artifact.read_text(encoding="utf-8")
            self.assertNotIn("private connection detail", persisted)
            self.assertIn('"failure_code": "vllm_unreachable"', persisted)


if __name__ == "__main__":
    import unittest

    unittest.main()
