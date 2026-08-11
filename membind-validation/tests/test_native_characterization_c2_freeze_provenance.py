"""Offline TDD contracts for safe C2 freeze-path provenance."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import native_characterization_c2 as c2  # noqa: E402
from native_characterization_c2_verify import (  # noqa: E402
    C2VerificationError,
    verify_c2_run,
)
from tests import test_native_characterization_c2 as runner_fixtures  # noqa: E402
from tests.test_native_characterization_c2_verify import (  # noqa: E402
    RUN_ID,
    _build_valid_run,
    _read_manifest,
    _rewrite_manifest,
)


SAFE_FREEZE_PATH = "fixtures/freezes/c2.json"


def _relocate_freeze(validation: Path, relative: str = SAFE_FREEZE_PATH) -> Path:
    source = validation / "artifacts/native_characterization/freeze.json"
    target = validation / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    source.replace(target)
    return target


def _set_manifest_freeze_path(validation: Path, relative: str) -> None:
    manifest = _read_manifest(validation)
    manifest["provenance"]["freeze_path"] = relative
    _rewrite_manifest(validation, manifest)


class NativeCharacterizationC2FreezeProvenanceTests(TestCase):
    def test_runner_binds_json_object_freeze_mode_into_default_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            validation = Path(temporary)
            freeze = runner_fixtures._write_freeze(validation)
            payload = json.loads(freeze.read_text(encoding="utf-8"))
            payload["construction_compatibility_policy"] = {
                "structured_output_mode": "json_object"
            }
            freeze.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
            captured: dict[str, object] = {}

            def factory(**kwargs):
                captured.update(kwargs)
                return runner_fixtures._fake_runtime_factory()

            with patch.object(c2, "build_u0_graphiti_from_env", side_effect=factory):
                result = asyncio.run(
                    c2.execute_c2(
                        validation_root=validation,
                        freeze_path="artifacts/native_characterization/freeze.json",
                        run_id="c2-offline-json-object-freeze",
                        authorization_checker=lambda _action: None,
                        measurement_installer=(
                            runner_fixtures._complete_measurement_installer
                        ),
                        graph_prefix_collector=(
                            runner_fixtures._graph_prefix_collector
                        ),
                    )
                )

            manifest = json.loads(
                (
                    validation
                    / "artifacts/native_characterization/runs"
                    / result["run_id"]
                    / "manifest.json"
                ).read_text(encoding="ascii")
            )
            self.assertEqual(captured["structured_output_mode"], "json_object")
            self.assertEqual(
                manifest["provenance"]["structured_output_mode"], "json_object"
            )

    def test_runner_rejects_invalid_freeze_mode_before_runtime_or_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            validation = Path(temporary)
            freeze = runner_fixtures._write_freeze(validation)
            payload = json.loads(freeze.read_text(encoding="utf-8"))
            payload["construction_compatibility_policy"] = {
                "structured_output_mode": "automatic"
            }
            freeze.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
            runtime_calls = 0

            def runtime_factory():
                nonlocal runtime_calls
                runtime_calls += 1
                return runner_fixtures._fake_runtime_factory()

            run_id = "c2-offline-invalid-freeze-mode"
            with self.assertRaises(c2.NativeCharacterizationC2Error) as raised:
                asyncio.run(
                    c2.execute_c2(
                        validation_root=validation,
                        freeze_path="artifacts/native_characterization/freeze.json",
                        run_id=run_id,
                        authorization_checker=lambda _action: None,
                        runtime_factory=runtime_factory,
                    )
                )
            self.assertEqual(str(raised.exception), "structured_output_mode_invalid")
            self.assertEqual(runtime_calls, 0)
            self.assertFalse(
                (
                    validation
                    / "artifacts/native_characterization/runs"
                    / run_id
                ).exists()
            )

    def test_runner_resolves_safe_nested_freeze_against_validation_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            validation = Path(temporary)
            runner_fixtures._write_freeze(validation)
            freeze = _relocate_freeze(validation)

            result = asyncio.run(
                c2.execute_c2(
                    validation_root=validation,
                    freeze_path=SAFE_FREEZE_PATH,
                    run_id="c2-offline-nested-freeze",
                    authorization_checker=lambda _action: None,
                    runtime_factory=runner_fixtures._fake_runtime_factory,
                    measurement_installer=(
                        runner_fixtures._complete_measurement_installer
                    ),
                    graph_prefix_collector=runner_fixtures._graph_prefix_collector,
                )
            )

            manifest_path = (
                validation
                / "artifacts/native_characterization/runs"
                / result["run_id"]
                / "manifest.json"
            )
            manifest = json.loads(manifest_path.read_text(encoding="ascii"))
            self.assertEqual(manifest["provenance"]["freeze_path"], SAFE_FREEZE_PATH)
            self.assertEqual(
                manifest["provenance"]["freeze_sha256"], c2._sha256_file(freeze)
            )

    def test_runner_rejects_unsafe_paths_before_runtime_or_artifact_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            validation = base / "validation"
            absolute_freeze = runner_fixtures._write_freeze(validation)
            outside = base / "outside.json"
            outside.write_bytes(absolute_freeze.read_bytes())
            cases = {
                "absolute": str(absolute_freeze),
                "traversal": "../outside.json",
                "backslash": "fixtures\\freeze.json",
                "noncanonical": "artifacts//native_characterization/freeze.json",
            }

            for label, supplied in cases.items():
                with self.subTest(label=label):
                    runtime_calls = 0

                    def runtime_factory():
                        nonlocal runtime_calls
                        runtime_calls += 1
                        return runner_fixtures._fake_runtime_factory()

                    run_id = f"c2-offline-unsafe-{label}"
                    with self.assertRaises(c2.NativeCharacterizationC2Error) as raised:
                        asyncio.run(
                            c2.execute_c2(
                                validation_root=validation,
                                freeze_path=supplied,
                                run_id=run_id,
                                authorization_checker=lambda _action: None,
                                runtime_factory=runtime_factory,
                            )
                        )
                    self.assertEqual(str(raised.exception), "freeze_path_invalid")
                    self.assertEqual(runtime_calls, 0)
                    self.assertFalse(
                        (
                            validation
                            / "artifacts/native_characterization/runs"
                            / run_id
                        ).exists()
                    )

    def test_runner_rejects_symlink_escape_before_runtime_or_artifact_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            validation = base / "validation"
            freeze = runner_fixtures._write_freeze(validation)
            outside = base / "outside.json"
            outside.write_bytes(freeze.read_bytes())
            link = validation / "fixtures/freezes/c2.json"
            link.parent.mkdir(parents=True)
            link.symlink_to(outside)
            runtime_calls = 0

            def runtime_factory():
                nonlocal runtime_calls
                runtime_calls += 1
                return runner_fixtures._fake_runtime_factory()

            run_id = "c2-offline-symlink-freeze"
            with self.assertRaises(c2.NativeCharacterizationC2Error) as raised:
                asyncio.run(
                    c2.execute_c2(
                        validation_root=validation,
                        freeze_path=SAFE_FREEZE_PATH,
                        run_id=run_id,
                        authorization_checker=lambda _action: None,
                        runtime_factory=runtime_factory,
                    )
                )
            self.assertEqual(str(raised.exception), "freeze_path_invalid")
            self.assertEqual(runtime_calls, 0)
            self.assertFalse(
                (
                    validation
                    / "artifacts/native_characterization/runs"
                    / run_id
                ).exists()
            )

    def test_verifier_uses_recorded_safe_nested_freeze_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            validation = Path(temporary)
            _build_valid_run(validation)
            _relocate_freeze(validation)
            _set_manifest_freeze_path(validation, SAFE_FREEZE_PATH)

            result = verify_c2_run(validation, RUN_ID)

            self.assertEqual(result["status"], "verified")

    def test_verifier_rechecks_recorded_nested_freeze_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            validation = Path(temporary)
            _build_valid_run(validation)
            freeze = _relocate_freeze(validation)
            _set_manifest_freeze_path(validation, SAFE_FREEZE_PATH)
            freeze.write_bytes(freeze.read_bytes() + b"tampered")

            with self.assertRaises(C2VerificationError) as raised:
                verify_c2_run(validation, RUN_ID)
            self.assertEqual(raised.exception.code, "provenance_local_hash_mismatch")

    def test_verifier_cross_checks_structured_output_mode_against_freeze(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            validation = Path(temporary)
            _build_valid_run(validation)
            manifest = _read_manifest(validation)
            manifest["provenance"]["structured_output_mode"] = "json_object"
            _rewrite_manifest(validation, manifest)

            with self.assertRaises(C2VerificationError) as raised:
                verify_c2_run(validation, RUN_ID)
            self.assertEqual(
                raised.exception.code,
                "structured_output_mode_cross_bind_mismatch",
            )

    def test_verifier_rejects_unsafe_recorded_freeze_paths(self) -> None:
        cases = {
            "absolute": lambda validation: str(
                validation / "artifacts/native_characterization/freeze.json"
            ),
            "traversal": lambda _validation: "../outside.json",
            "backslash": lambda _validation: "fixtures\\freeze.json",
            "noncanonical": lambda _validation: (
                "artifacts//native_characterization/freeze.json"
            ),
        }
        for label, build_value in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                validation = Path(temporary)
                _build_valid_run(validation)
                _set_manifest_freeze_path(validation, build_value(validation))

                with self.assertRaises(C2VerificationError) as raised:
                    verify_c2_run(validation, RUN_ID)
                self.assertEqual(
                    raised.exception.code, "provenance_freeze_path_invalid"
                )

    def test_verifier_rejects_recorded_freeze_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            validation = base / "validation"
            _build_valid_run(validation)
            original = validation / "artifacts/native_characterization/freeze.json"
            outside = base / "outside.json"
            outside.write_bytes(original.read_bytes())
            link = validation / SAFE_FREEZE_PATH
            link.parent.mkdir(parents=True)
            link.symlink_to(outside)
            _set_manifest_freeze_path(validation, SAFE_FREEZE_PATH)

            with self.assertRaises(C2VerificationError) as raised:
                verify_c2_run(validation, RUN_ID)
            self.assertEqual(raised.exception.code, "provenance_freeze_path_invalid")


if __name__ == "__main__":
    import unittest

    unittest.main()
