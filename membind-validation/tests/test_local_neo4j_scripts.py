from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class LocalNeo4jScriptContractTests(unittest.TestCase):
    def test_documented_start_script_defaults_to_daemon(self) -> None:
        script = (ROOT / "scripts" / "start_local_neo4j.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("start_local_neo4j_daemon.sh", script)
        self.assertIn("--console", script)

    def test_daemon_requires_sustained_process_and_port_health(self) -> None:
        script = (ROOT / "scripts" / "start_local_neo4j_daemon.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn('"$NEO4J_HOME/bin/neo4j" status', script)
        self.assertIn("stable_checks", script)
        self.assertIn("REQUIRED_STABLE_CHECKS", script)


if __name__ == "__main__":
    unittest.main()
