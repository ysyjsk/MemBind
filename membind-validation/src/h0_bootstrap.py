"""Process bootstrap for H0's explicit credential-loading boundary."""

from __future__ import annotations

import os


def disable_implicit_dotenv() -> None:
    """Disable dependency-owned config discovery and external telemetry."""

    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    os.environ["GRAPHITI_TELEMETRY_ENABLED"] = "false"


disable_implicit_dotenv()
