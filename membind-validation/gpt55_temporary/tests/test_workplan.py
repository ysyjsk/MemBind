from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[2]


class GPT55TemporaryWorkplanTests(TestCase):
    """Keep the temporary GPT/LabForge lane visible without moving mainline gates."""

    def test_workplan_declares_temporary_scope_and_no_mainline_pass(self):
        workplan = (ROOT / "gpt55_temporary" / "WORKPLAN.md").read_text(encoding="utf-8")

        self.assertIn("temporary diagnostic lane", workplan)
        self.assertIn("does not advance V3/V4/V5/V6", workplan)
        self.assertIn("do not reuse partial v3_smoke_001 cache", workplan)
        self.assertIn("User-Agent: OpenAI/Python 1.0.0", workplan)
        self.assertIn("/chat/completions", workplan)
        self.assertIn("TDD checkpoints", workplan)
        self.assertIn("temporary Graphiti factory", workplan)
        self.assertIn("local_bge_m3", workplan)
        self.assertIn("BAAI/bge-m3", workplan)
        self.assertIn("/data/predator/ly/Mem/cache/huggingface/hub", workplan)
        self.assertNotIn("CONSTRUCTION_LLM_PROVIDER=openai_chat", workplan)

    def test_global_memory_indexes_temporary_files_and_artifacts(self):
        memory = (ROOT / "GLOBAL_MEMORY.md").read_text(encoding="utf-8")

        self.assertIn("gpt55_temporary/WORKPLAN.md", memory)
        self.assertIn("gpt55_temporary/scripts/labforge_gateway_probe.py", memory)
        self.assertIn("gpt55_temporary/scripts/local_embedding_adapter.py", memory)
        self.assertIn("gpt55_temporary/tests/test_labforge_gateway_probe.py", memory)
        self.assertIn("v3_smoke_001", memory)
        self.assertIn("mainline vLLM protocol remains frozen", memory)
