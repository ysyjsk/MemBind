"""Subprocess contracts for disabling Graphiti's implicit dotenv side effect."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
H0_IMPORT_ENTRY_POINTS = (
    "h0_artifacts",
    "h0_control",
    "h0_executor",
    "h0_live_preflight",
    "h0_live_runner",
    "h0_phase_runner",
    "h0_runtime",
    "h0_state_transition",
)


class H0BootstrapTests(TestCase):
    def test_every_h0_import_disables_implicit_dotenv_before_graphiti_import(self):
        probe = """
import importlib
import json
import os
import sys

import dotenv.main

dotenv.main.find_dotenv = lambda *args, **kwargs: sys.argv[2]
os.environ.pop("MEMBIND_H0_IMPORT_SENTINEL", None)
importlib.import_module(sys.argv[1])
print(json.dumps({
    "dotenv_disabled": os.environ.get("PYTHON_DOTENV_DISABLED"),
    "graphiti_telemetry_enabled": os.environ.get("GRAPHITI_TELEMETRY_ENABLED"),
    "sentinel_loaded": "MEMBIND_H0_IMPORT_SENTINEL" in os.environ,
}, sort_keys=True))
"""
        with tempfile.TemporaryDirectory() as tmp:
            sentinel = Path(tmp) / ".env"
            sentinel.write_text(
                "MEMBIND_H0_IMPORT_SENTINEL=loaded\n", encoding="utf-8"
            )
            for module in H0_IMPORT_ENTRY_POINTS:
                with self.subTest(module=module):
                    environment = dict(os.environ)
                    environment.pop("PYTHON_DOTENV_DISABLED", None)
                    environment.pop("MEMBIND_H0_IMPORT_SENTINEL", None)
                    environment["PYTHONPATH"] = str(ROOT / "src")
                    completed = subprocess.run(
                        [sys.executable, "-c", probe, module, str(sentinel)],
                        cwd=ROOT,
                        env=environment,
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    observed = json.loads(completed.stdout)
                    self.assertEqual(observed["dotenv_disabled"], "1")
                    self.assertEqual(
                        observed["graphiti_telemetry_enabled"], "false"
                    )
                    self.assertFalse(observed["sentinel_loaded"])

    def test_bootstrap_prevents_graphiti_telemetry_client_and_cache_creation(self):
        probe = """
import json
import sys
from pathlib import Path

import h0_bootstrap
import graphiti_core.telemetry.telemetry as telemetry

cache = Path(sys.argv[1]) / "graphiti"
telemetry.CACHE_DIR = cache
telemetry.ANON_ID_FILE = cache / "telemetry_anon_id"
initializations = []
telemetry.initialize_posthog = lambda: initializations.append(True)
telemetry.capture_event("h0_offline_probe")
print(json.dumps({
    "cache_exists": cache.exists(),
    "initialization_count": len(initializations),
    "telemetry_enabled": telemetry.is_telemetry_enabled(),
}, sort_keys=True))
"""
        with tempfile.TemporaryDirectory() as tmp:
            environment = dict(os.environ)
            environment.pop("PYTHON_DOTENV_DISABLED", None)
            environment.pop("GRAPHITI_TELEMETRY_ENABLED", None)
            environment["PYTHONPATH"] = str(ROOT / "src")
            completed = subprocess.run(
                [sys.executable, "-c", probe, tmp],
                cwd=ROOT,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )

        observed = json.loads(completed.stdout)
        self.assertFalse(observed["telemetry_enabled"])
        self.assertEqual(observed["initialization_count"], 0)
        self.assertFalse(observed["cache_exists"])


if __name__ == "__main__":
    import unittest

    unittest.main()
