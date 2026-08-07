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


class ContractSourceTests(TestCase):
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
