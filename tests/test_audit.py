"""Run with: pytest tests/"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import mrp_audit as audit  # noqa: E402


def test_finds_exactly_the_planted_errors(tmp_path):
    report = audit.run(ROOT / "sample_data", tmp_path, quiet=True)
    key = pd.read_csv(ROOT / "sample_data" / "answer_key.csv")
    assert set(zip(report.part, report.issue)) == set(zip(key.part, key.issue))


def test_recommendations_match_ground_truth(tmp_path):
    report = audit.run(ROOT / "sample_data", tmp_path, quiet=True)
    key = pd.read_csv(ROOT / "sample_data" / "answer_key.csv")
    m = key.merge(report, on=["part", "issue"])
    for _, r in m.iterrows():
        if r.issue == "OPTION_CODE":
            assert r.recommended == r.correct
        else:
            assert abs(float(r.recommended) - float(r.correct)) < 1e-9


def test_invalid_phase_is_flagged():
    data = {
        "parts": pd.DataFrame([{"part": "CHG-KR", "market": "KR", "option_codes": "KR+1PH|KR+3PH"}]),
        "market_rules": pd.DataFrame([{"market": "KR", "region": "APAC", "charging_phase": "1PH"}]),
        "mrp_vehicle_demand": pd.DataFrame([
            {"week": "W1", "part": "CHG-KR", "option_key": "KR+1PH", "vehicle_demand": 100},
            {"week": "W1", "part": "CHG-KR", "option_key": "KR+3PH", "vehicle_demand": 49},
        ]),
    }
    out = audit.check_option_codes(data)
    assert list(out.recommended) == ["KR+1PH"]
    assert out.phantom_vehicles_per_week.iloc[0] == 49


def test_inventory_formula():
    data = {"inventory_weekly": pd.DataFrame([
        {"week": "W1", "part": "P", "on_hand": 100, "in_transit": 120, "actual_consumption": 100},
        {"week": "W2", "part": "P", "on_hand": 120, "in_transit": 120, "actual_consumption": 100},
    ])}
    inv = audit.inventory_trend(data)
    assert list(inv.ending_calc) == [120, 140]
    assert inv.wow_change.iloc[1] == 20
