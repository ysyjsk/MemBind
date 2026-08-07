import os
import sys
import tempfile
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from graphiti_native import load_env_file  # noqa: E402


class EnvConfigTests(TestCase):
    def test_env_loader_reads_local_env_without_overriding_existing_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "VLLM_API_KEY=secret\n"
                "CONSTRUCTION_LLM_BASE_URL=http://10.87.5.247:8000/v1/\n"
                "EMBEDDING_BASE_URL=http://10.87.5.247:8001/v1\n",
                encoding="utf-8",
            )
            old = os.environ.get("VLLM_API_KEY")
            os.environ["VLLM_API_KEY"] = "already-set"
            try:
                loaded = load_env_file(env_path)
                self.assertEqual(loaded["EMBEDDING_BASE_URL"], "http://10.87.5.247:8001/v1")
                self.assertEqual(os.environ["VLLM_API_KEY"], "already-set")
            finally:
                if old is None:
                    os.environ.pop("VLLM_API_KEY", None)
                else:
                    os.environ["VLLM_API_KEY"] = old

    def test_gitignore_excludes_real_env_but_allows_example(self):
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".env", gitignore)
        self.assertIn("!.env.example", gitignore)

    def test_env_loader_merges_private_model_host_into_existing_no_proxy(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "NO_PROXY=127.0.0.1,localhost,10.87.5.247\n"
                "no_proxy=127.0.0.1,localhost,10.87.5.247\n",
                encoding="utf-8",
            )
            old_upper = os.environ.get("NO_PROXY")
            old_lower = os.environ.get("no_proxy")
            os.environ["NO_PROXY"] = "localhost"
            os.environ["no_proxy"] = "localhost"
            try:
                load_env_file(env_path)
                self.assertIn("10.87.5.247", os.environ["NO_PROXY"].split(","))
                self.assertIn("10.87.5.247", os.environ["no_proxy"].split(","))
                self.assertIn("localhost", os.environ["NO_PROXY"].split(","))
            finally:
                if old_upper is None:
                    os.environ.pop("NO_PROXY", None)
                else:
                    os.environ["NO_PROXY"] = old_upper
                if old_lower is None:
                    os.environ.pop("no_proxy", None)
                else:
                    os.environ["no_proxy"] = old_lower
