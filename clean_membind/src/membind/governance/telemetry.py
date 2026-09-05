from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping


class JsonlTelemetry:
    """Append-only, fsynced telemetry for long-running runs."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = self.path.open("a", encoding="utf-8")

    def append(self, event: Mapping[str, Any]) -> None:
        self._stream.write(json.dumps(dict(event), ensure_ascii=False, sort_keys=True, default=str) + "\n")
        self._stream.flush()
        os.fsync(self._stream.fileno())

    def close(self) -> None:
        self._stream.close()

    def __enter__(self) -> "JsonlTelemetry":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
