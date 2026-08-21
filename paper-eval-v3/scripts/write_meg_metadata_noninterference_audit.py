from __future__ import annotations

import json
from pathlib import Path

from paper_eval.membind_v4.mseg.metadata_audit import (
    audit_metadata_noninterference,
    render_metadata_noninterference_audit,
)


def main() -> int:
    project = Path(__file__).resolve().parents[1]
    audit = audit_metadata_noninterference(project)
    output = project / "artifacts/paper_eval/membind_v4/meg_runtime_instrumentation"
    output.mkdir(parents=True, exist_ok=True)
    (output / "MEG_METADATA_NONINTERFERENCE_AUDIT.md").write_text(
        render_metadata_noninterference_audit(audit), encoding="utf-8"
    )
    (output / "MEG_METADATA_NONINTERFERENCE_AUDIT.json").write_text(
        json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0 if audit["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
