#!/usr/bin/env python3
"""Generate the pinned Graphiti 0.29.3 MEG runtime qualification bundle."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT / "src"
DEFAULT_GRAPHITI_ROOT = (
    PROJECT.parent
    / "membind-validation/.venv/lib/python3.12/site-packages/graphiti_core"
)
DEFAULT_OUTPUT_ROOT = (
    PROJECT
    / "artifacts/paper_eval/membind_v4/meg_runtime_instrumentation"
    / "meg-runtime-offline-20260821-001"
)
DEFAULT_BOUNDARY_AUDIT_DOC = PROJECT / "GRAPHITI_0293_SEMANTIC_BOUNDARY_AUDIT.md"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=PROJECT)
    parser.add_argument("--graphiti-root", type=Path, default=DEFAULT_GRAPHITI_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument(
        "--boundary-audit-doc", type=Path, default=DEFAULT_BOUNDARY_AUDIT_DOC
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    project = Path(args.project_root).resolve()
    graphiti = Path(args.graphiti_root).resolve()
    output = Path(args.output_root).resolve()
    boundary_doc = Path(args.boundary_audit_doc).resolve()
    if output.exists():
        raise ValueError("meg_runtime_offline_output_root_not_fresh")
    site_packages = graphiti.parent
    for path in (SOURCE, site_packages):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))

    from paper_eval.artifacts import atomic_write_json
    from paper_eval.membind_v4.mseg.runtime_qualification import (
        build_runtime_instrumentation_documents,
    )

    documents = build_runtime_instrumentation_documents(
        project_root=project,
        graphiti_root=graphiti,
    )
    output.mkdir(parents=True, exist_ok=False)
    for name, value in documents.items():
        destination = output / name
        if isinstance(value, dict):
            atomic_write_json(destination, value)
        else:
            destination.write_text(value, encoding="utf-8")
    audit_markdown = documents["GRAPHITI_0293_SEMANTIC_BOUNDARY_AUDIT.md"]
    assert isinstance(audit_markdown, str)
    boundary_doc.parent.mkdir(parents=True, exist_ok=True)
    boundary_doc.write_text(audit_markdown, encoding="utf-8")

    qualification = documents["MEG_RUNTIME_INSTRUMENTATION_QUALIFICATION.json"]
    assert isinstance(qualification, dict)
    print(json.dumps(qualification["decision"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
