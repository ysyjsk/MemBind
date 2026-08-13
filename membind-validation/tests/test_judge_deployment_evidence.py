"""RED contracts for sealed, evidence-derived Judge deployment identity.

These tests use a synthetic validation root and never perform network I/O.
They define the production loader that must replace caller-asserted model
identity before a formal Judge run is authorized.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from evaluation.judge_qualification import canonical_json_bytes  # noqa: E402
from evaluation.judge_qualification_live import (  # noqa: E402
    load_verified_judge_deployment_evidence,
)


ACTUAL_REVISION = "aa55da1ecc13d006e8b8e4f54579b1ea8c3db2df"
HISTORICAL_C2_REVISION = "6e2312b85c2ae9a31f629f24493b79d8b02eab1a"
CHAT_TEMPLATE_SHA256 = (
    "a55ee1b1660128b7098723e0abcd92caa0788061051c62d51cbe87d9cf1974d8"
)
RUNTIME = {
    "served_model_name": "qwen3-32b-fp8",
    "vllm_version": "0.26.0",
    "repository_revision": ACTUAL_REVISION,
    "dtype": "bfloat16",
    "quantization": "fp8",
    "max_model_len": 65536,
    "rope_parameters": {
        "rope_type": "yarn",
        "factor": 2.0,
        "original_max_position_embeddings": 32768,
        "rope_theta": 1000000,
    },
    "chat_template_sha256": CHAT_TEMPLATE_SHA256,
}
MISMATCH = {
    "classification": "historical_c2_revision_differs_from_current_deployment",
    "historical_repository_revision": HISTORICAL_C2_REVISION,
    "current_repository_revision": ACTUAL_REVISION,
}
EVIDENCE_PATHS = {
    "serving_envelope": (
        "artifacts/environment/"
        "native_characterization_64k_serving_envelope_20260812.json"
    ),
    "reference_aligned_freeze": (
        "artifacts/native_characterization/freeze_reference_aligned_64k.json"
    ),
    "completed_c2_manifest": (
        "artifacts/native_characterization/runs/"
        "c2-17cdaabd562e9673/manifest.json"
    ),
    "restricted_remote_observation": (
        "artifacts/environment/judge_restricted_remote_observation_20260813.json"
    ),
}


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _seal(value: dict[str, object]) -> dict[str, object]:
    sealed = deepcopy(value)
    sealed.pop("payload_sha256", None)
    sealed["payload_sha256"] = _sha256(canonical_json_bytes(sealed))
    return sealed


def _write_canonical(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value) + b"\n")


class SyntheticDeploymentEvidence:
    """Build the smallest mutually cross-checking production evidence tree."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.sources: dict[str, dict[str, object]] = {
            "serving_envelope": _seal(
                {
                    "schema_version": "membind.native-characterization-64k-envelope.v1",
                    "qualification_status": "64K_ENVELOPE_PASS",
                    "runtime": {
                        "served_model_id": RUNTIME["served_model_name"],
                        "vllm_version": RUNTIME["vllm_version"],
                        "max_model_len": RUNTIME["max_model_len"],
                        "rope_type": "yarn",
                        "yarn_factor": 2.0,
                        "original_max_position_embeddings": 32768,
                        "rope_theta": 1000000,
                    },
                }
            ),
            "reference_aligned_freeze": _seal(
                {
                    "schema_version": "membind.native-characterization-freeze.v1",
                    "runtime_identities": {
                        "construction": {
                            "served_model_id": RUNTIME["served_model_name"],
                            "vllm_version": RUNTIME["vllm_version"],
                            "model_revision": HISTORICAL_C2_REVISION,
                            "dtype": RUNTIME["dtype"],
                            "quantization": RUNTIME["quantization"],
                            "max_model_len": RUNTIME["max_model_len"],
                            "enable_thinking": False,
                            "rope_type": "yarn",
                            "yarn_factor": 2.0,
                            "original_max_position_embeddings": 32768,
                            "rope_theta": 1000000,
                        }
                    },
                }
            ),
            "completed_c2_manifest": _seal(
                {
                    "schema_version": "membind.native-characterization-c2-result.v1",
                    "status": "completed",
                    "run_id": "c2-17cdaabd562e9673",
                    "telemetry_completeness": {
                        "status": "complete",
                        "missing_required_fields": [],
                    },
                    "provenance": {
                        "sanitized_runtime_identity": {
                            "construction": {
                                "served_model_id": RUNTIME["served_model_name"],
                                "vllm_version": RUNTIME["vllm_version"],
                                "model_revision": HISTORICAL_C2_REVISION,
                                "dtype": RUNTIME["dtype"],
                                "quantization": RUNTIME["quantization"],
                                "max_model_len": RUNTIME["max_model_len"],
                                "enable_thinking": False,
                                "rope_type": "yarn",
                                "yarn_factor": 2.0,
                                "original_max_position_embeddings": 32768,
                                "rope_theta": 1000000,
                            }
                        }
                    },
                }
            ),
            "restricted_remote_observation": _seal(
                {
                    "schema_version": "membind.judge-restricted-remote-observation.v1",
                    "access_mode": "ssh_forced_command_read_only",
                    "observation_scope": "/home/lhx/liuyi/**",
                    "runtime": deepcopy(RUNTIME),
                    "model_fingerprint_status": "not_observed_no_actual_scan",
                }
            ),
        }
        self._write_sources()
        self.evidence = self._build_evidence()
        self.evidence_path = (
            root / "artifacts/environment/judge_deployment_evidence_20260813.json"
        )
        _write_canonical(self.evidence_path, self.evidence)

    def _write_sources(self) -> None:
        for name, value in self.sources.items():
            _write_canonical(self.root / EVIDENCE_PATHS[name], value)

    def _build_evidence(self) -> dict[str, object]:
        return _seal(
            {
                "schema_version": "membind.judge-deployment-evidence.v1",
                "scientific_surface": "JUDGE_QUALIFICATION_ONLY",
                "runtime": deepcopy(RUNTIME),
                "historical_c2_revision_mismatch": deepcopy(MISMATCH),
                "evidence_bindings": {
                    name: {
                        "path": relative,
                        "sha256": _sha256((self.root / relative).read_bytes()),
                    }
                    for name, relative in EVIDENCE_PATHS.items()
                },
            }
        )

    @property
    def evidence_sha256(self) -> str:
        return _sha256(self.evidence_path.read_bytes())

    def rewrite_evidence(self, value: dict[str, object]) -> None:
        self.evidence = value
        _write_canonical(self.evidence_path, value)

    def load(self) -> object:
        return load_verified_judge_deployment_evidence(
            self.root,
            self.evidence_path,
            self.evidence_sha256,
        )


class JudgeDeploymentEvidenceTests(TestCase):
    def test_loads_exact_cross_checked_identity_without_fabricated_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            tree = SyntheticDeploymentEvidence(Path(temporary))
            loaded = tree.load()

        expected = deepcopy(RUNTIME)
        expected["historical_c2_revision_mismatch"] = MISMATCH
        expected["evidence_bindings"] = tree.evidence["evidence_bindings"]
        expected["evidence_payload_sha256"] = tree.evidence["payload_sha256"]
        self.assertEqual(dict(loaded), expected)
        self.assertEqual(loaded["repository_revision"], ACTUAL_REVISION)
        self.assertNotIn("model_fingerprint", loaded)

    def test_outer_seal_and_expected_file_hash_are_independent_gates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            tree = SyntheticDeploymentEvidence(Path(temporary))
            broken_seal = deepcopy(tree.evidence)
            broken_seal["runtime"]["dtype"] = "float16"
            tree.rewrite_evidence(broken_seal)
            with self.assertRaises((ValueError, RuntimeError)):
                load_verified_judge_deployment_evidence(
                    tree.root, tree.evidence_path, tree.evidence_sha256
                )

            tree.rewrite_evidence(tree._build_evidence())
            with self.assertRaises((ValueError, RuntimeError)):
                load_verified_judge_deployment_evidence(
                    tree.root, tree.evidence_path, "0" * 64
                )

    def test_outer_artifact_bytes_must_be_canonical(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            tree = SyntheticDeploymentEvidence(Path(temporary))
            tree.evidence_path.write_text(
                json.dumps(tree.evidence, indent=2) + "\n",
                encoding="ascii",
            )
            with self.assertRaises((ValueError, RuntimeError)):
                load_verified_judge_deployment_evidence(
                    tree.root,
                    tree.evidence_path,
                    _sha256(tree.evidence_path.read_bytes()),
                )

    def test_bound_source_byte_drift_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            tree = SyntheticDeploymentEvidence(Path(temporary))
            bound = tree.root / EVIDENCE_PATHS["completed_c2_manifest"]
            bound.write_bytes(bound.read_bytes() + b" ")
            with self.assertRaises((ValueError, RuntimeError)):
                tree.load()

    def test_binding_names_and_paths_are_exact_not_substitutable(self) -> None:
        mutations = ("renamed_binding", "same_bytes_different_path")
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                tree = SyntheticDeploymentEvidence(Path(temporary))
                changed = deepcopy(tree.evidence)
                bindings = changed["evidence_bindings"]
                if mutation == "renamed_binding":
                    bindings["c2"] = bindings.pop("completed_c2_manifest")
                else:
                    original = tree.root / EVIDENCE_PATHS["serving_envelope"]
                    substitute = original.with_name("same-serving-envelope.json")
                    substitute.write_bytes(original.read_bytes())
                    bindings["serving_envelope"] = {
                        "path": substitute.relative_to(tree.root).as_posix(),
                        "sha256": _sha256(substitute.read_bytes()),
                    }
                tree.rewrite_evidence(_seal(changed))
                with self.assertRaises((ValueError, RuntimeError)):
                    tree.load()

    def test_runtime_tuple_and_chat_template_must_match_remote_observation(self) -> None:
        mutations = {
            "served_model_name": "another-model",
            "vllm_version": "0.25.0",
            "repository_revision": "b" * 40,
            "dtype": "float16",
            "quantization": "none",
            "max_model_len": 40960,
            "rope_parameters": {
                "rope_type": "linear",
                "factor": 1.0,
                "original_max_position_embeddings": 16384,
                "rope_theta": 10000,
            },
            "chat_template_sha256": "c" * 64,
        }
        for field, replacement in mutations.items():
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                tree = SyntheticDeploymentEvidence(Path(temporary))
                changed = deepcopy(tree.evidence)
                changed["runtime"][field] = replacement
                tree.rewrite_evidence(_seal(changed))
                with self.assertRaises((ValueError, RuntimeError)):
                    tree.load()

    def test_self_consistent_wrong_actual_identity_is_still_rejected(self) -> None:
        for field, replacement in (
            ("repository_revision", "b" * 40),
            ("chat_template_sha256", "c" * 64),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                tree = SyntheticDeploymentEvidence(Path(temporary))
                remote = deepcopy(tree.sources["restricted_remote_observation"])
                remote["runtime"][field] = replacement
                remote = _seal(remote)
                remote_path = tree.root / EVIDENCE_PATHS["restricted_remote_observation"]
                _write_canonical(remote_path, remote)

                changed = deepcopy(tree.evidence)
                changed["runtime"][field] = replacement
                if field == "repository_revision":
                    changed["historical_c2_revision_mismatch"][
                        "current_repository_revision"
                    ] = replacement
                changed["evidence_bindings"]["restricted_remote_observation"][
                    "sha256"
                ] = _sha256(remote_path.read_bytes())
                tree.rewrite_evidence(_seal(changed))
                with self.assertRaises((ValueError, RuntimeError)):
                    tree.load()

    def test_model_fingerprint_without_actual_scan_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            tree = SyntheticDeploymentEvidence(Path(temporary))
            changed = deepcopy(tree.evidence)
            changed["runtime"]["model_fingerprint"] = "f" * 64
            tree.rewrite_evidence(_seal(changed))
            with self.assertRaises((ValueError, RuntimeError)):
                tree.load()

    def test_historical_revision_mismatch_must_be_explicit_and_exact(self) -> None:
        mutations = {
            "hidden": None,
            "misclassified": {
                **MISMATCH,
                "classification": "revisions_match",
            },
            "historical_drift": {
                **MISMATCH,
                "historical_repository_revision": "d" * 40,
            },
            "current_drift": {
                **MISMATCH,
                "current_repository_revision": "e" * 40,
            },
        }
        for label, replacement in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                tree = SyntheticDeploymentEvidence(Path(temporary))
                changed = deepcopy(tree.evidence)
                if replacement is None:
                    changed.pop("historical_c2_revision_mismatch")
                else:
                    changed["historical_c2_revision_mismatch"] = replacement
                tree.rewrite_evidence(_seal(changed))
                with self.assertRaises((ValueError, RuntimeError)):
                    tree.load()

    def test_historical_enable_thinking_must_be_explicitly_false(self) -> None:
        for binding_name in ("reference_aligned_freeze", "completed_c2_manifest"):
            with self.subTest(binding=binding_name), tempfile.TemporaryDirectory() as temporary:
                tree = SyntheticDeploymentEvidence(Path(temporary))
                source = deepcopy(tree.sources[binding_name])
                if binding_name == "reference_aligned_freeze":
                    construction = source["runtime_identities"]["construction"]
                else:
                    construction = source["provenance"]["sanitized_runtime_identity"][
                        "construction"
                    ]
                construction["enable_thinking"] = True
                source = _seal(source)
                source_path = tree.root / EVIDENCE_PATHS[binding_name]
                _write_canonical(source_path, source)
                changed = deepcopy(tree.evidence)
                changed["evidence_bindings"][binding_name]["sha256"] = _sha256(
                    source_path.read_bytes()
                )
                tree.rewrite_evidence(_seal(changed))
                with self.assertRaises((ValueError, RuntimeError)):
                    tree.load()

    def test_outer_and_bound_source_symlinks_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            tree = SyntheticDeploymentEvidence(Path(temporary))
            real = tree.evidence_path
            link = real.with_name("deployment-link.json")
            link.symlink_to(real.name)
            with self.assertRaises((ValueError, RuntimeError)):
                load_verified_judge_deployment_evidence(
                    tree.root, link, _sha256(real.read_bytes())
                )

        with tempfile.TemporaryDirectory() as temporary:
            tree = SyntheticDeploymentEvidence(Path(temporary))
            bound = tree.root / EVIDENCE_PATHS["restricted_remote_observation"]
            real = bound.with_name("remote-real.json")
            bound.rename(real)
            bound.symlink_to(real.name)
            with self.assertRaises((ValueError, RuntimeError)):
                tree.load()


if __name__ == "__main__":
    import unittest

    unittest.main()
