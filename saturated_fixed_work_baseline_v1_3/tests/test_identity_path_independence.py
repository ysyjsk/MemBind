from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
IDENTITY = (
    ROOT / "saturated_fixed_work_baseline_v1_3/src/"
    "saturated_fixed_work_baseline_v1_3/membind_v6_1/identity.py"
)


def _module():
    spec = importlib.util.spec_from_file_location("identity_under_test", IDENTITY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_component_payload_hash_is_independent_of_absolute_checkout_path(tmp_path: Path) -> None:
    module = _module()
    first = tmp_path / "checkout-a" / "component"
    second = tmp_path / "checkout-b" / "component"
    for root in (first, second):
        (root / "nested").mkdir(parents=True)
        (root / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
        (root / "nested/b.py").write_text("VALUE = 2\n", encoding="utf-8")

    first_identity = module._component("example", first)
    second_identity = module._component("example", second)

    assert first_identity["payload_sha256"] == second_identity["payload_sha256"]
    assert first_identity["path"] != second_identity["path"]
    assert first_identity["files"] == second_identity["files"]


def test_source_epoch_errors_fail_closed_on_dirty_or_drift() -> None:
    module = _module()
    assert module.source_epoch_errors(
        expected_head="head-a",
        expected_source_bundle_sha256="bundle-a",
        observed={
            "git_head": "head-a",
            "dirty_paths": [],
            "source_bundle_sha256": "bundle-a",
        },
    ) == []
    errors = module.source_epoch_errors(
        expected_head="head-a",
        expected_source_bundle_sha256="bundle-a",
        observed={
            "git_head": "head-b",
            "dirty_paths": [" M changed.py"],
            "source_bundle_sha256": "bundle-b",
        },
    )
    assert any("HEAD drift" in error for error in errors)
    assert any("dirty" in error for error in errors)
    assert any("source bundle drift" in error for error in errors)


def test_implementation_bundle_hash_excludes_component_locator_paths(tmp_path: Path) -> None:
    module = _module()
    module._component = lambda name, path: {
        "name": name,
        "files": {"module.py": "f" * 64},
        "path": str(Path(path).resolve()),
        "payload_sha256": "c" * 64,
    }
    module._module_source = lambda name: tmp_path / name
    first = module.implementation_bundle(tmp_path / "runner-a.py")
    second = module.implementation_bundle(tmp_path / "runner-b.py")
    assert first["payload_sha256"] == second["payload_sha256"]
