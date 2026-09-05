"""Validate backend wiring without changing Graphiti or benchmark semantics."""

from __future__ import annotations

import argparse
import json

from ..backends import BackendConfig


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="emit machine-readable identity")
    args = parser.parse_args()
    payload = {"status": "CONFIG_ONLY", "backend": BackendConfig().to_dict(), "backend_identity_sha256": BackendConfig().identity_sha256}
    print(json.dumps(payload, sort_keys=True) if args.json else json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
