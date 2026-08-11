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

    def test_global_memory_preserves_temporary_lane_exclusion_fence(self):
        memory = (ROOT / "GLOBAL_MEMORY.md").read_text(encoding="utf-8")

        self.assertIn("`gpt55_temporary/**` exclusion remain unchanged", memory)

    def test_active_bounded_lane_documents_model_gate_and_black_box_latency(self):
        workplan = (ROOT / "gpt55_temporary" / "WORKPLAN.md").read_text(
            encoding="utf-8"
        )
        methodology = (
            ROOT / "gpt55_temporary" / "API_LATENCY_METHODOLOGY.md"
        ).read_text(encoding="utf-8")

        for required in (
            "gpt-5.4-mini",
            "structured Chat preflight",
            "preflight before dataset/GPU/Neo4j/Graphiti",
            "does not advance V3/V4/V5/V6",
        ):
            self.assertIn(required, workplan)
        for required in (
            "client_observed_remote_api_wait",
            "time-to-rejection",
            "TTFT = unavailable",
            "ITL/TPOT = unavailable",
            "interval union",
            "DistServe",
            "Llumnix",
            "Parrot",
            "Clockwork",
            "Clipper",
            "vLLM",
        ):
            self.assertIn(required, methodology)
        self.assertNotIn("403 inference latency", methodology)

    def test_current_result_report_persists_the_gateway_blocker(self):
        report = (
            ROOT
            / "gpt55_temporary"
            / "artifacts"
            / "diagnostics"
            / "gpt54mini_bounded_001_report.md"
        ).read_text(encoding="utf-8")

        self.assertIn("HTTP 403", report)
        self.assertIn("gpt-5.4-mini", report)
        self.assertIn("RTX 3090 Ti", report)
        self.assertIn("19/19", report)
        self.assertIn("mainline state unchanged", report)
        self.assertIn("No Graphiti episode was executed", report)
