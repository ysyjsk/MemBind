import hashlib
import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from formal_gate import validate_formal_gate  # noqa: E402


class FormalGateTests(TestCase):
    def test_complete_gate_accepts_only_frozen_prerequisites(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts, data_path, plan = _fixture(Path(tmp))

            result = validate_formal_gate(artifacts, data_path, plan, smoke_attempt="smoke-pass")

            self.assertTrue(result["ok"])
            self.assertEqual(result["failures"], [])

    def test_contract_must_be_exactly_twenty_successes(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts, data_path, plan = _fixture(Path(tmp))
            status_path = artifacts / "environment" / "integration_gate_status.json"
            status = json.loads(status_path.read_text(encoding="utf-8"))
            status["remote_construction_contract"]["structured_success"] = 19
            status_path.write_text(json.dumps(status), encoding="utf-8")

            result = validate_formal_gate(artifacts, data_path, plan, smoke_attempt="smoke-pass")

            self.assertFalse(result["ok"])
            self.assertTrue(any("20/20" in failure for failure in result["failures"]))

    def test_contract_requires_frozen_remote_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts, data_path, plan = _fixture(Path(tmp))
            status_path = artifacts / "environment" / "integration_gate_status.json"
            status = json.loads(status_path.read_text(encoding="utf-8"))
            status["remote_construction_contract"]["runtime_contract_ok"] = False
            status_path.write_text(json.dumps(status), encoding="utf-8")

            result = validate_formal_gate(artifacts, data_path, plan, smoke_attempt="smoke-pass")

            self.assertFalse(result["ok"])
            self.assertTrue(any("runtime" in failure for failure in result["failures"]))

    def test_smoke_must_have_m2_parity_and_no_unexpected_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts, data_path, plan = _fixture(Path(tmp))
            smoke_path = artifacts / "smoke" / "smoke-pass.json"
            smoke = json.loads(smoke_path.read_text(encoding="utf-8"))
            smoke["m2_vs_m0"]["canonical_graph_parity"] = False
            smoke_path.write_text(json.dumps(smoke), encoding="utf-8")

            result = validate_formal_gate(artifacts, data_path, plan, smoke_attempt="smoke-pass")

            self.assertFalse(result["ok"])
            self.assertTrue(any("smoke" in failure for failure in result["failures"]))

    def test_smoke_requires_explicit_exactly_once_and_source_order_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts, data_path, plan = _fixture(Path(tmp))
            smoke_path = artifacts / "smoke" / "smoke-pass.json"
            smoke = json.loads(smoke_path.read_text(encoding="utf-8"))
            smoke.pop("source_order")
            smoke_path.write_text(json.dumps(smoke), encoding="utf-8")

            result = validate_formal_gate(artifacts, data_path, plan, smoke_attempt="smoke-pass")

            self.assertFalse(result["ok"])
            self.assertTrue(any("smoke" in failure for failure in result["failures"]))

    def test_replay_dependency_must_name_prior_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts, data_path, plan = _fixture(Path(tmp))
            replay = next(item for item in plan if item["mode"] == "replay")
            replay["depends_on"] = "missing-capture"

            result = validate_formal_gate(artifacts, data_path, plan, smoke_attempt="smoke-pass")

            self.assertFalse(result["ok"])
            self.assertTrue(any("depends_on" in failure for failure in result["failures"]))

    def test_split_hash_must_match_input_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts, data_path, plan = _fixture(Path(tmp))
            data_path.write_text(data_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

            result = validate_formal_gate(artifacts, data_path, plan, smoke_attempt="smoke-pass")

            self.assertFalse(result["ok"])
            self.assertTrue(any("SHA256" in failure for failure in result["failures"]))

    def test_recorded_source_sha_artifact_is_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts, data_path, plan = _fixture(Path(tmp))
            (artifacts / "dataset" / "source_sha256.txt").unlink()

            result = validate_formal_gate(artifacts, data_path, plan, smoke_attempt="smoke-pass")

            self.assertFalse(result["ok"])
            self.assertTrue(any("SHA256" in failure for failure in result["failures"]))

    def test_unresolved_environment_blocker_rejects_formal_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts, data_path, plan = _fixture(Path(tmp))
            blocker = artifacts / "environment" / "construction_context_blocker.json"
            blocker.write_text(
                json.dumps({"status": "blocked", "formal_gate_allowed": False}),
                encoding="utf-8",
            )

            result = validate_formal_gate(artifacts, data_path, plan, smoke_attempt="smoke-pass")

            self.assertFalse(result["ok"])
            self.assertTrue(any("blocker" in failure for failure in result["failures"]))


def _fixture(root: Path) -> tuple[Path, Path, list[dict[str, object]]]:
    artifacts = root / "artifacts"
    environment = artifacts / "environment"
    smoke_dir = artifacts / "smoke"
    dataset_dir = artifacts / "dataset"
    for path in (environment, smoke_dir, dataset_dir):
        path.mkdir(parents=True)

    data_path = root / "dataset.json"
    data_path.write_text(json.dumps([{"question_id": "q0"}]), encoding="utf-8")
    source_sha = hashlib.sha256(data_path.read_bytes()).hexdigest()
    (dataset_dir / "source_sha256.txt").write_text(source_sha + "\n", encoding="utf-8")
    (dataset_dir / "frozen_split.json").write_text(
        json.dumps({"source_sha256": source_sha, "source_path": str(data_path)}),
        encoding="utf-8",
    )
    status = {
        "ok": True,
        "remote_construction_contract": {
            "ok": True,
            "runtime_contract_ok": True,
            "structured_checks": 20,
            "structured_success": 20,
        },
    }
    (environment / "integration_gate_status.json").write_text(json.dumps(status), encoding="utf-8")
    (smoke_dir / "smoke-pass.json").write_text(
        json.dumps(
            {
                "ok": True,
                "m2_vs_m0": {"canonical_graph_parity": True},
                "unexpected_prompt": False,
                "source_order": {
                    "M2": {
                        "exactly_once": True,
                        "source_order_violation": False,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    plan: list[dict[str, object]] = []
    for qid in (f"q{i}" for i in range(8)):
        capture_id = f"capture-{qid}"
        plan.append(
            {
                "run_id": capture_id,
                "question_id": qid,
                "lane": "correctness",
                "method": "M0",
                "mode": "capture",
                "repeat": 0,
            }
        )
        plan.append(
            {
                "run_id": f"replay-{qid}",
                "question_id": qid,
                "lane": "correctness",
                "method": "M2",
                "mode": "replay",
                "repeat": 0,
                "depends_on": capture_id,
            }
        )
    for qid in (f"q{i}" for i in range(8)):
        for method in ("M0", "M1", "M2"):
            for repeat in (0, 1):
                plan.append(
                    {
                        "run_id": f"perf-{qid}-{method}-{repeat}",
                        "question_id": qid,
                        "lane": "performance",
                        "method": method,
                        "mode": "live",
                        "repeat": repeat,
                    }
                )
    return artifacts, data_path, plan
