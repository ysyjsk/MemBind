import json
import re
import sys
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from graphiti_native import DEFAULT_CONSTRUCTION_MODEL  # noqa: E402


def _parse_simple_yaml(path):
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        values[key.strip()] = value.strip()
    return values


def _parse_env_contract(path):
    wanted = {
        "CONSTRUCTION_LLM_BASE_URL",
        "CONSTRUCTION_LLM_MODEL",
        "CONSTRUCTION_EXPECTED_VLLM_VERSION",
        "CONSTRUCTION_MIN_CONTEXT_TOKENS",
        "EMBEDDING_BASE_URL",
        "EMBEDDING_MODEL",
    }
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key in wanted:
            values[key] = value.strip().strip('"').strip("'")
    return values


def _parse_plan_contract(path):
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(
        r"^- (?P<name>construction|embedding): (?P<endpoint>\S+), model (?P<model>[^;\n]+);",
        re.MULTILINE,
    )
    return {
        match.group("name"): {
            "endpoint": match.group("endpoint"),
            "model": match.group("model").strip(),
        }
        for match in pattern.finditer(text)
    }


def _parse_nested_yaml_contract(path):
    """Parse the scalar/list subset used by the frozen experiment config."""

    result = {}
    section = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indentation = len(raw_line) - len(raw_line.lstrip())
        key, separator, value = raw_line.strip().partition(":")
        if not separator:
            continue
        if indentation == 0 and not value.strip():
            section = key
            result[section] = {}
            continue
        target = result.setdefault(section, {}) if indentation else result
        scalar = value.strip()
        if scalar in {"true", "false"}:
            parsed = scalar == "true"
        elif scalar.startswith("[") and scalar.endswith("]"):
            parsed = [
                float(item) if "." in item else int(item)
                for item in scalar[1:-1].split(",")
                if item.strip()
            ]
        else:
            try:
                parsed = float(scalar) if "." in scalar else int(scalar)
            except ValueError:
                parsed = scalar
        target[key] = parsed
    return result


class ContractSourceTests(TestCase):
    def test_vllm_0_26_structured_output_contract_has_immutable_upstream_sources(self):
        evidence = json.loads(
            (
                ROOT
                / "artifacts"
                / "environment"
                / "vllm_0_26_structured_output_contract_20260809.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(evidence["tag"], "v0.26.0")
        self.assertEqual(evidence["tag_commit"], "568afb3a13806beb53bb2e6bd518269357b237c0")
        self.assertEqual(evidence["default_backend"], "auto")
        self.assertIn("xgrammar", evidence["supported_backends"])
        self.assertIn("guidance", evidence["supported_backends"])
        self.assertTrue(evidence["response_format_json_schema_is_normalized"])
        self.assertFalse(evidence["deployed_backend_proven"])
        self.assertGreaterEqual(len(evidence["sources"]), 5)
        for source in evidence["sources"]:
            self.assertRegex(source["sha256"], r"^[0-9a-f]{64}$")

    def test_plan_base_config_and_env_use_same_model_services(self):
        plan = _parse_plan_contract(ROOT / "EXPERIMENT_PLAN.md")
        base = _parse_simple_yaml(ROOT / "configs" / "base.yaml")
        env = _parse_env_contract(ROOT / ".env")

        expected = {
            "construction": {
                "endpoint": base["construction_llm_base_url"],
                "model": base["construction_llm_model"],
            },
            "embedding": {
                "endpoint": base["embedding_base_url"],
                "model": base["embedding_model"],
            },
        }
        from_env = {
            "construction": {
                "endpoint": env["CONSTRUCTION_LLM_BASE_URL"],
                "model": env["CONSTRUCTION_LLM_MODEL"],
            },
            "embedding": {
                "endpoint": env["EMBEDDING_BASE_URL"],
                "model": env["EMBEDDING_MODEL"],
            },
        }

        self.assertEqual(plan, expected)
        self.assertEqual(from_env, expected)

    def test_example_env_and_code_default_match_served_construction_model(self):
        base = _parse_simple_yaml(ROOT / "configs" / "base.yaml")
        example = _parse_env_contract(ROOT / ".env.example")

        self.assertEqual(base["construction_llm_model"], "qwen3-32b-fp8")
        self.assertEqual(
            example["CONSTRUCTION_LLM_MODEL"],
            base["construction_llm_model"],
        )
        self.assertEqual(DEFAULT_CONSTRUCTION_MODEL, base["construction_llm_model"])

    def test_runtime_contract_sources_freeze_user_approved_vllm(self):
        base = _parse_simple_yaml(ROOT / "configs" / "base.yaml")
        private_env = _parse_env_contract(ROOT / ".env")
        example_env = _parse_env_contract(ROOT / ".env.example")

        self.assertEqual(base["construction_expected_vllm_version"], "0.26.0")
        self.assertEqual(base["construction_min_context_tokens"], "40960")
        for env in (private_env, example_env):
            self.assertEqual(env["CONSTRUCTION_EXPECTED_VLLM_VERSION"], "0.26.0")
            self.assertEqual(env["CONSTRUCTION_MIN_CONTEXT_TOKENS"], "40960")

    def test_prompt_candidate_canonicalization_contract_is_synced(self):
        base = _parse_simple_yaml(ROOT / "configs" / "base.yaml")
        contract = "logical_content_ascending_after_top_k"

        self.assertEqual(base["prompt_candidate_order"], contract)
        for path in (
            ROOT / "README.md",
            ROOT / "EXPERIMENT_PLAN.md",
            ROOT.parent / "MemBind_basic_validation_experiment.md",
        ):
            self.assertIn(contract, path.read_text(encoding="utf-8"))

    def test_node_candidate_canonicalization_contract_is_synced(self):
        base = _parse_simple_yaml(ROOT / "configs" / "base.yaml")
        contract = "logical_content_ascending_before_candidate_id"

        self.assertEqual(base["prompt_node_candidate_order"], contract)
        for path in (
            ROOT / "README.md",
            ROOT / "EXPERIMENT_PLAN.md",
            ROOT.parent / "MemBind_basic_validation_experiment.md",
        ):
            self.assertIn(contract, path.read_text(encoding="utf-8"))

    def test_edge_search_cutoff_tie_break_contract_is_synced(self):
        base = _parse_simple_yaml(ROOT / "configs" / "base.yaml")
        contract = "logical_content_ascending_before_top_k"

        self.assertEqual(base["prompt_edge_search_tie_break"], contract)
        for path in (
            ROOT / "README.md",
            ROOT / "EXPERIMENT_PLAN.md",
            ROOT.parent / "MemBind_basic_validation_experiment.md",
        ):
            self.assertIn(contract, path.read_text(encoding="utf-8"))

    def test_node_search_cutoff_tie_break_contract_is_synced(self):
        base = _parse_simple_yaml(ROOT / "configs" / "base.yaml")
        contract = "logical_node_content_ascending_before_top_k"

        self.assertEqual(base["prompt_node_search_tie_break"], contract)
        for path in (
            ROOT / "README.md",
            ROOT / "EXPERIMENT_PLAN.md",
            ROOT.parent / "MemBind_basic_validation_experiment.md",
        ):
            self.assertIn(contract, path.read_text(encoding="utf-8"))

    def test_v1_1_measurement_and_characterization_config_is_frozen(self):
        config = _parse_nested_yaml_contract(ROOT / "configs" / "base.yaml")

        self.assertEqual(
            config["measurement"],
            {
                "network_gate": True,
                "network_probe_count": 20,
                "telemetry_interval_s": 1.0,
                "block_randomization_seed": 20260806,
                "reset_prefix_cache_between_runs": True,
                "reset_embedding_cache_between_runs": True,
                "instrumentation_overhead_limit": 0.02,
            },
        )
        self.assertEqual(
            config["characterization"],
            {
                "enabled": True,
                "concurrency_levels": [1, 2, 4, 8],
                "load_rho": [0.5, 1.0, 1.5],
                "poisson_sensitivity": True,
            },
        )

    def test_v1_1_replacement_clauses_are_retained_as_history_only(self):
        protocol = (ROOT.parent / "MemBind_basic_validation_experiment.md").read_text(
            encoding="utf-8"
        )

        required_contracts = (
            "Pilot Protocol v1.1",
            "Phase 4.5",
            "correctness smoke",
            "interval union",
            "network_baseline.json",
            "baseline_med + 5 * max(baseline_mad, 0.1 ms)",
            "reset_prefix_cache",
            "cold cross-run",
            "block = (question_id, repeat)",
            "entire block",
            "instrumentation_overhead_limit: 0.02",
            "Upstream-Native-Serial",
            "Deterministic-Native-Serial",
        )
        for contract in required_contracts:
            with self.subTest(contract=contract):
                self.assertIn(contract, protocol)

        self.assertIn("global shuffle", protocol)
        self.assertRegex(
            protocol,
            r"(?s)global shuffle.*(?:replaced|替换).*blocked randomization",
        )
        self.assertIn("历史背景；非当前执行计划", protocol)
        self.assertIn("不得再被解释为当前", protocol)
        self.assertIn("最终只输出 GO / INCONCLUSIVE / NO-GO", protocol)
        self.assertNotIn("MECHANISM_SUPPORTED", protocol)

    def test_v1_1_does_not_change_frozen_core_contracts(self):
        protocol = (ROOT.parent / "MemBind_basic_validation_experiment.md").read_text(
            encoding="utf-8"
        )

        for frozen in (
            "021d3a5",
            "Evidence Fence",
            "temperature: 0.0",
            "seed: 20260806",
            "P95 `arrival_to_publish_ms`",
            "canonical semantic graph",
        ):
            with self.subTest(frozen=frozen):
                self.assertIn(frozen, protocol)
