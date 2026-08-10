"""Offline contracts for state-gated H0 project credential parsing.

The tests use temporary files only.  They prove that parsing never mutates the
process environment and that every runtime endpoint/model is checked against
the already-authorized definition before a client can be constructed.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from h0_credentials import H0ProjectCredentialLoader  # noqa: E402
from h0_runtime import H0ManifestError  # noqa: E402


class H0ProjectCredentialLoaderTests(TestCase):
    def _definition(self) -> SimpleNamespace:
        return SimpleNamespace(
            identity={
                "base_url": "http://10.87.5.247:8000/v1/",
                "served_model_id": "qwen3-32b-fp8",
            },
            embedding_namespace={
                "served_model_id": "qwen3-embedding-0.6b",
            },
        )

    def _write_env(self, root: Path, *, construction_model: str = "qwen3-32b-fp8") -> None:
        (root / ".env").write_text(
            "# private runtime configuration\n"
            "CONSTRUCTION_LLM_API_KEY=construction-secret\n"
            "CONSTRUCTION_LLM_BASE_URL=http://10.87.5.247:8000/v1/\n"
            f"CONSTRUCTION_LLM_MODEL={construction_model}\n"
            "EMBEDDING_API_KEY=embedding-secret\n"
            "EMBEDDING_BASE_URL=http://10.87.5.247:8001/v1\n"
            "EMBEDDING_MODEL=qwen3-embedding-0.6b\n"
            "NEO4J_URI=bolt://localhost:7687\n"
            "NEO4J_USER=neo4j\n"
            "NEO4J_PASSWORD=database-secret\n",
            encoding="ascii",
        )

    def test_loads_all_three_sections_without_mutating_process_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_env(root)
            sentinel = {"NO_PROXY": "unchanged", "UNRELATED": "preserved"}
            with patch.dict(os.environ, sentinel, clear=True):
                before = dict(os.environ)
                loaded = H0ProjectCredentialLoader(
                    root=root,
                    definition=self._definition(),
                )()
                self.assertEqual(dict(os.environ), before)

            self.assertEqual(
                loaded,
                {
                    "construction": {
                        "base_url": "http://10.87.5.247:8000/v1/",
                        "api_key": "construction-secret",
                    },
                    "embedding": {
                        "base_url": "http://10.87.5.247:8001/v1/",
                        "model": "qwen3-embedding-0.6b",
                        "api_key": "embedding-secret",
                    },
                    "neo4j": {
                        "uri": "bolt://localhost:7687",
                        "user": "neo4j",
                        "password": "database-secret",
                        "database": "neo4j",
                    },
                },
            )

    def test_shared_api_key_fallback_is_explicit_and_does_not_escape_return_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text(
                "VLLM_API_KEY=shared-secret\n"
                "CONSTRUCTION_LLM_BASE_URL=http://10.87.5.247:8000/v1/\n"
                "CONSTRUCTION_LLM_MODEL=qwen3-32b-fp8\n"
                "EMBEDDING_BASE_URL=http://10.87.5.247:8001/v1\n"
                "EMBEDDING_MODEL=qwen3-embedding-0.6b\n"
                "NEO4J_URI=bolt://localhost:7687\n"
                "NEO4J_USER=neo4j\n"
                "NEO4J_PASSWORD=database-secret\n",
                encoding="ascii",
            )
            with patch.dict(os.environ, {}, clear=True):
                loaded = H0ProjectCredentialLoader(
                    root=root,
                    definition=self._definition(),
                )()
                self.assertNotIn("VLLM_API_KEY", os.environ)
            self.assertEqual(loaded["construction"]["api_key"], "shared-secret")
            self.assertEqual(loaded["embedding"]["api_key"], "shared-secret")

    def test_rejects_duplicate_keys_and_binding_drift_without_mutating_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_env(root, construction_model="wrong-model")
            with patch.dict(os.environ, {"SENTINEL": "unchanged"}, clear=True):
                before = dict(os.environ)
                with self.assertRaises(H0ManifestError):
                    H0ProjectCredentialLoader(
                        root=root,
                        definition=self._definition(),
                    )()
                self.assertEqual(dict(os.environ), before)

            (root / ".env").write_text(
                "CONSTRUCTION_LLM_API_KEY=first\n"
                "CONSTRUCTION_LLM_API_KEY=second\n",
                encoding="ascii",
            )
            with self.assertRaisesRegex(H0ManifestError, "duplicate"):
                H0ProjectCredentialLoader(
                    root=root,
                    definition=self._definition(),
                )()

    def test_requires_a_regular_project_env_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(H0ManifestError):
                H0ProjectCredentialLoader(
                    root=root,
                    definition=self._definition(),
                )()

