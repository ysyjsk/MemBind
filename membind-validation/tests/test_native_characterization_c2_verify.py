"""Offline tamper-verification contracts for completed C2 evidence."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from native_characterization_c2_verify import (  # noqa: E402
    C2VerificationError,
    main,
    verify_c2_run,
)


RUN_ID = "c2-0123456789abcdef"
JSONL_NAMES = (
    "spans.jsonl",
    "llm.jsonl",
    "embedding.jsonl",
    "db.jsonl",
    "events.jsonl",
    "errors.jsonl",
)
CODE_BINDINGS = {
    "c2_runner_source_sha256": "src/native_characterization_c2.py",
    "measurement_adapter_source_sha256": (
        "src/native_characterization_c2_measurement.py"
    ),
    "base_instrumentation_source_sha256": (
        "src/native_characterization_instrumentation.py"
    ),
    "tracing_source_sha256": "src/native_characterization_tracing.py",
}


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _seal(value: dict[str, object]) -> dict[str, object]:
    sealed = dict(value)
    sealed.pop("payload_sha256", None)
    sealed["payload_sha256"] = _sha(_canonical(sealed))
    return sealed


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical(value) + b"\n")


def _write_jsonl(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical(_seal(value)) + b"\n")


def _file_sha(path: Path) -> str:
    return _sha(path.read_bytes())


def _inventory(run_root: Path) -> tuple[dict[str, str], dict[str, object]]:
    hashes: dict[str, str] = {}
    inventory: dict[str, object] = {}
    for path in sorted(
        item
        for item in run_root.rglob("*")
        if item.is_file() and item.name != "manifest.json"
    ):
        relative = path.relative_to(run_root).as_posix()
        raw = path.read_bytes()
        digest = _sha(raw)
        hashes[relative] = digest
        inventory[relative] = {
            "sha256": digest,
            "byte_count": len(raw),
            "line_count": raw.count(b"\n") if path.suffix == ".jsonl" else None,
        }
    return hashes, inventory


def _manifest_path(validation: Path) -> Path:
    return (
        validation
        / "artifacts"
        / "native_characterization"
        / "runs"
        / RUN_ID
        / "manifest.json"
    )


def _read_manifest(validation: Path) -> dict[str, object]:
    return json.loads(_manifest_path(validation).read_text("ascii"))


def _rewrite_manifest(validation: Path, manifest: dict[str, object]) -> None:
    _write_json(_manifest_path(validation), _seal(manifest))


def _refresh_inventory(validation: Path) -> None:
    manifest = _read_manifest(validation)
    run_root = _manifest_path(validation).parent
    hashes, inventory = _inventory(run_root)
    manifest["artifact_sha256"] = hashes
    manifest["artifact_inventory"] = inventory
    _rewrite_manifest(validation, manifest)


def _build_valid_run(validation: Path) -> Path:
    run_root = _manifest_path(validation).parent
    run_root.mkdir(parents=True)

    freeze = validation / "artifacts/native_characterization/freeze.json"
    phase_map = validation / "artifacts/native_characterization/phase_map.json"
    _write_json(
        freeze,
        {
            "schema_version": "freeze.fixture.v1",
            "construction_compatibility_policy": {
                "structured_output_mode": "json_schema"
            },
        },
    )
    _write_json(phase_map, {"schema_version": "phase-map.fixture.v1"})
    for field, relative in CODE_BINDINGS.items():
        path = validation / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# fixture for {field}\n", encoding="ascii")

    checkpoint = _seal(
        {
            "schema_version": "membind.native-characterization-c2-checkpoint.v1",
            "run_id": RUN_ID,
            "stage": "C2",
            "status": "completed",
            "completed_block_indices": [0],
            "completed_episode_ids": ["history:0"],
            "planned_block_indices": [0],
            "checkpoint_history": [],
            "error_code": None,
        }
    )
    breakdown = _seal(
        {
            "schema_version": "membind.native-characterization-e1-breakdown.v1",
            "run_id": RUN_ID,
            "freeze_sha256": _file_sha(freeze),
            "telemetry_completeness": {
                "status": "complete",
                "missing_required_fields": [],
            },
            "blocks": [],
            "aggregate": {},
            "aggregate_phase_occupancy": {},
            "interpretation": "bounded_screening_not_significance_claim",
        }
    )
    _write_json(run_root / "checkpoint.json", checkpoint)
    _write_json(run_root / "e1_breakdown.json", breakdown)
    top_level_e1 = validation / "artifacts/native_characterization/e1_breakdown.json"
    _write_json(top_level_e1, breakdown)

    for name in JSONL_NAMES:
        view = name.removesuffix(".jsonl")
        _write_jsonl(
            run_root / name,
            {
                "schema_version": (
                    f"membind.native-characterization-c2-{view}.v1"
                ),
                "run_id": RUN_ID,
                "episode_id": "history:0",
                "source_sequence": 0,
                "episode_source_sha256": "1" * 64,
                "prefix_sha256": "2" * 64,
                "spans": [],
            },
        )

    block = run_root / "blocks/000_0123abcd"
    _write_jsonl(
        block / "trace.jsonl",
        {
            "schema_version": "membind.native-characterization.trace.v1",
            "run_id": RUN_ID,
            "episode_id": "history:0",
            "source_sequence": 0,
            "episode_source_sha256": "1" * 64,
            "prefix_sha256": "2" * 64,
            "spans": [],
        },
    )
    _write_json(block / "checkpoint.json", checkpoint)
    _write_json(
        block / "block_summary.json",
        _seal(
            {
                "schema_version": (
                    "membind.native-characterization-c2-block-summary.v1"
                ),
                "run_id": RUN_ID,
                "block_index": 0,
                "history_id": "0123abcd",
                "graph_namespace": "nc-e1e2-0000000000000000",
                "episode_count": 1,
                "span_count": 0,
                "freeze_sha256": _file_sha(freeze),
                "counters": {},
            }
        ),
    )

    artifact_hashes, artifact_inventory = _inventory(run_root)
    provenance: dict[str, object] = {
        "creation_command": (
            ".venv/bin/python src/native_characterization_c2.py --live "
            f"--run-id {RUN_ID}"
        ),
        "freeze_path": "artifacts/native_characterization/freeze.json",
        "freeze_sha256": _file_sha(freeze),
        "structured_output_mode": "json_schema",
        "phase_map_sha256": _file_sha(phase_map),
        "frozen_input_hashes": {},
        "dataset_source_sha256": "3" * 64,
        "sanitized_runtime_identity": {},
    }
    for field, relative in CODE_BINDINGS.items():
        provenance[field] = _file_sha(validation / relative)
    manifest = _seal(
        {
            "schema_version": "membind.native-characterization-c2-result.v1",
            "run_id": RUN_ID,
            "stage": "C2",
            "status": "completed",
            "freeze_sha256": _file_sha(freeze),
            "e1_breakdown_sha256": _file_sha(run_root / "e1_breakdown.json"),
            "top_level_e1_breakdown_sha256": _file_sha(top_level_e1),
            "checkpoint_sha256": _file_sha(run_root / "checkpoint.json"),
            "block_count": 1,
            "episode_count": 1,
            "telemetry_completeness": {
                "status": "complete",
                "missing_required_fields": [],
            },
            "provenance": provenance,
            "artifact_sha256": artifact_hashes,
            "artifact_inventory": artifact_inventory,
            "interpretation": "bounded_screening_not_significance_claim",
        }
    )
    _write_json(run_root / "manifest.json", manifest)
    return run_root


class NativeCharacterizationC2VerifierTests(TestCase):
    def test_valid_run_verifies_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            validation = Path(temporary)
            run_root = _build_valid_run(validation)
            before = {
                path.relative_to(validation).as_posix(): path.read_bytes()
                for path in validation.rglob("*")
                if path.is_file()
            }

            result = verify_c2_run(validation, RUN_ID)

            after = {
                path.relative_to(validation).as_posix(): path.read_bytes()
                for path in validation.rglob("*")
                if path.is_file()
            }
            self.assertEqual(result["status"], "verified")
            self.assertEqual(result["run_id"], RUN_ID)
            self.assertEqual(result["indexed_file_count"], len(_inventory(run_root)[0]))
            self.assertEqual(after, before)

    def test_manifest_payload_inventory_and_file_seals_fail_closed(self) -> None:
        mutations = {
            "manifest_payload_hash_mismatch": self._break_manifest_payload,
            "artifact_index_mismatch": self._break_inventory_agreement,
            "artifact_hash_mismatch": self._tamper_checkpoint,
            "artifact_byte_count_mismatch": self._break_byte_count,
            "artifact_line_count_mismatch": self._break_line_count,
        }
        for code, mutate in mutations.items():
            with self.subTest(code=code), tempfile.TemporaryDirectory() as temporary:
                validation = Path(temporary)
                _build_valid_run(validation)
                mutate(validation)
                with self.assertRaises(C2VerificationError) as raised:
                    verify_c2_run(validation, RUN_ID)
                self.assertEqual(raised.exception.code, code)

    def test_added_and_deleted_files_fail_closed(self) -> None:
        for code, mutate in (
            ("artifact_set_mismatch", lambda root: (root / "UNINDEXED").write_text("x")),
            ("artifact_set_mismatch", lambda root: (root / "db.jsonl").unlink()),
        ):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as temporary:
                validation = Path(temporary)
                run_root = _build_valid_run(validation)
                mutate(run_root)
                with self.assertRaises(C2VerificationError) as raised:
                    verify_c2_run(validation, RUN_ID)
                self.assertEqual(raised.exception.code, code)

    def test_every_indexed_file_is_sealed_against_tamper_and_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as template_directory:
            template = Path(template_directory)
            run_root = _build_valid_run(template)
            relatives = sorted(_inventory(run_root)[0])

        for relative in relatives:
            for operation, expected_code in (
                ("tamper", "artifact_hash_mismatch"),
                ("delete", "artifact_set_mismatch"),
            ):
                with (
                    self.subTest(relative=relative, operation=operation),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    validation = Path(temporary)
                    run_root = _build_valid_run(validation)
                    target = run_root / relative
                    if operation == "tamper":
                        target.write_bytes(target.read_bytes() + b"x")
                    else:
                        target.unlink()
                    with self.assertRaises(C2VerificationError) as raised:
                        verify_c2_run(validation, RUN_ID)
                    self.assertEqual(raised.exception.code, expected_code)

    def test_jsonl_payload_schema_and_run_id_fail_closed_after_outer_reseal(self) -> None:
        for code, field, value, reseal_line in (
            ("jsonl_payload_hash_mismatch", "episode_id", "changed", False),
            ("jsonl_schema_invalid", "schema_version", "wrong.v1", True),
            ("jsonl_run_id_mismatch", "run_id", "c2-fedcba9876543210", True),
        ):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as temporary:
                validation = Path(temporary)
                run_root = _build_valid_run(validation)
                path = run_root / "spans.jsonl"
                envelope = json.loads(path.read_text("ascii"))
                envelope[field] = value
                if reseal_line:
                    envelope = _seal(envelope)
                path.write_bytes(_canonical(envelope) + b"\n")
                _refresh_inventory(validation)
                with self.assertRaises(C2VerificationError) as raised:
                    verify_c2_run(validation, RUN_ID)
                self.assertEqual(raised.exception.code, code)

    def test_checkpoint_and_e1_cross_bindings_fail_closed(self) -> None:
        for code, field in (
            ("checkpoint_cross_bind_mismatch", "checkpoint_sha256"),
            ("e1_cross_bind_mismatch", "e1_breakdown_sha256"),
        ):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as temporary:
                validation = Path(temporary)
                _build_valid_run(validation)
                manifest = _read_manifest(validation)
                manifest[field] = "0" * 64
                _rewrite_manifest(validation, manifest)
                with self.assertRaises(C2VerificationError) as raised:
                    verify_c2_run(validation, RUN_ID)
                self.assertEqual(raised.exception.code, code)

    def test_local_provenance_and_top_level_e1_are_rechecked(self) -> None:
        mutations = {
            "provenance_local_hash_mismatch": lambda root: (
                root / "src/native_characterization_c2.py"
            ).write_text("# modified\n"),
            "top_level_e1_cross_bind_mismatch": lambda root: (
                root / "artifacts/native_characterization/e1_breakdown.json"
            ).write_bytes(b"{}\n"),
        }
        for code, mutate in mutations.items():
            with self.subTest(code=code), tempfile.TemporaryDirectory() as temporary:
                validation = Path(temporary)
                _build_valid_run(validation)
                mutate(validation)
                with self.assertRaises(C2VerificationError) as raised:
                    verify_c2_run(validation, RUN_ID)
                self.assertEqual(raised.exception.code, code)

    def test_each_freeze_phase_map_and_code_provenance_binding_is_rechecked(self) -> None:
        relatives = [
            "artifacts/native_characterization/freeze.json",
            "artifacts/native_characterization/phase_map.json",
            *CODE_BINDINGS.values(),
        ]
        for relative in relatives:
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as temporary:
                validation = Path(temporary)
                _build_valid_run(validation)
                target = validation / relative
                target.write_bytes(target.read_bytes() + b"x")
                with self.assertRaises(C2VerificationError) as raised:
                    verify_c2_run(validation, RUN_ID)
                self.assertEqual(
                    raised.exception.code, "provenance_local_hash_mismatch"
                )

    def test_errors_are_sanitized_and_cli_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            validation = Path(temporary)
            _build_valid_run(validation)
            manifest_path = _manifest_path(validation)
            manifest_path.write_text('{"PRIVATE_SECRET":', encoding="ascii")
            with self.assertRaises(C2VerificationError) as raised:
                verify_c2_run(validation, RUN_ID)
            self.assertEqual(str(raised.exception), "manifest_json_invalid")
            self.assertNotIn("PRIVATE", str(raised.exception))

        with tempfile.TemporaryDirectory() as temporary:
            validation = Path(temporary)
            _build_valid_run(validation)
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exit_code = main(
                    ["--validation-root", str(validation), "--run-id", RUN_ID]
                )
            self.assertEqual(exit_code, 0)
            self.assertEqual(json.loads(stdout.getvalue())["status"], "verified")
            self.assertEqual(stderr.getvalue(), "")

    @staticmethod
    def _break_manifest_payload(validation: Path) -> None:
        manifest = _read_manifest(validation)
        manifest["status"] = "tampered"
        _write_json(_manifest_path(validation), manifest)

    @staticmethod
    def _break_inventory_agreement(validation: Path) -> None:
        manifest = _read_manifest(validation)
        manifest["artifact_sha256"]["checkpoint.json"] = "0" * 64  # type: ignore[index]
        _rewrite_manifest(validation, manifest)

    @staticmethod
    def _tamper_checkpoint(validation: Path) -> None:
        checkpoint = _manifest_path(validation).parent / "checkpoint.json"
        checkpoint.write_bytes(checkpoint.read_bytes() + b" ")

    @staticmethod
    def _break_byte_count(validation: Path) -> None:
        manifest = _read_manifest(validation)
        manifest["artifact_inventory"]["checkpoint.json"]["byte_count"] += 1  # type: ignore[index,operator]
        _rewrite_manifest(validation, manifest)

    @staticmethod
    def _break_line_count(validation: Path) -> None:
        manifest = _read_manifest(validation)
        manifest["artifact_inventory"]["spans.jsonl"]["line_count"] += 1  # type: ignore[index,operator]
        _rewrite_manifest(validation, manifest)


if __name__ == "__main__":
    import unittest

    unittest.main()
