from paper_eval.roles import build_role_registry


def test_calibration_and_inspected_ids_are_development_exposed() -> None:
    registry = build_role_registry(
        calibration_ids=["07741c45", "b6019101"],
        inspected_ids=["b6019101", "c6853660"],
        pilot_ids=["pilot-a"],
        final_ids=["final-a"],
    )
    assert registry["DEVELOPMENT_EXPOSED"] == ["07741c45", "b6019101", "c6853660"]
    assert registry["PILOT"] == ["pilot-a"]
    assert registry["FINAL_PAPER_TEST"] == ["final-a"]

