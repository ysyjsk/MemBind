#!/usr/bin/env python3
"""Deterministically finalize the main methodology Markdown document."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any


PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT / "src"))

from paper_eval.methodology_document import (
    MethodologyDocumentError,
    render_methodology_document,
)


def _read_object(path: Path, *, label: str) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise MethodologyDocumentError(f"{label} has duplicate fields")
            result[key] = value
        return result

    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicates
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise MethodologyDocumentError(f"{label} is unreadable") from None
    if not isinstance(value, dict):
        raise MethodologyDocumentError(f"{label} is invalid")
    return value


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        raise MethodologyDocumentError("report file is unreadable") from None
    return digest.hexdigest()


def _atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


def finalize_document(
    *, document_path: Path, report_path: Path, decision_path: Path, output_path: Path
) -> str:
    try:
        document = document_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise MethodologyDocumentError("methodology document is unreadable") from None
    report = _read_object(report_path, label="report")
    decision = _read_object(decision_path, label="decision")
    bindings = decision.get("input_bindings")
    if not isinstance(bindings, dict) or bindings.get(
        "report_file_sha256"
    ) != _file_sha256(report_path):
        raise MethodologyDocumentError("report file binding drift")
    rendered = render_methodology_document(document, report, decision)
    if output_path.exists() and output_path != document_path:
        try:
            existing = output_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            raise MethodologyDocumentError("existing output is unreadable") from None
        if existing != rendered:
            raise MethodologyDocumentError("existing output drift")
        return rendered
    if rendered != document:
        _atomic_write_text(output_path, rendered)
    return rendered


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bind the main methodology document to sealed evidence."
    )
    parser.add_argument("--document", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--decision", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    output = args.document if args.output is None else args.output
    try:
        rendered = finalize_document(
            document_path=args.document,
            report_path=args.report,
            decision_path=args.decision,
            output_path=output,
        )
    except BaseException as error:
        print(
            "STOP methodology_document "
            f"error_class={type(error).__module__}.{type(error).__qualname__}",
            flush=True,
        )
        return 1
    digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    print(f"PASS methodology_document sha256={digest} output={output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
