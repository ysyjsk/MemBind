"""Outcome-independent benchmark role registry for the v3 protocol."""

from __future__ import annotations

from collections.abc import Iterable


ROLE_NAMES = ("DEVELOPMENT_EXPOSED", "PILOT", "FINAL_PAPER_TEST")


def _normalized(values: Iterable[str]) -> list[str]:
    return sorted({str(value) for value in values})


def build_role_registry(
    *,
    calibration_ids: Iterable[str] = (),
    inspected_ids: Iterable[str] = (),
    pilot_ids: Iterable[str] = (),
    final_ids: Iterable[str] = (),
) -> dict[str, list[str]]:
    registry = {
        "DEVELOPMENT_EXPOSED": _normalized([*calibration_ids, *inspected_ids]),
        "PILOT": _normalized(pilot_ids),
        "FINAL_PAPER_TEST": _normalized(final_ids),
    }
    sets = {name: set(values) for name, values in registry.items()}
    overlaps = {
        f"{left}:{right}": sorted(sets[left] & sets[right])
        for index, left in enumerate(ROLE_NAMES)
        for right in ROLE_NAMES[index + 1 :]
        if sets[left] & sets[right]
    }
    if overlaps:
        raise ValueError(f"benchmark data-role overlap: {overlaps}")
    return registry

