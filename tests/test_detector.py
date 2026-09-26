"""
Lightweight sanity tests for the discrepancy detector's core math.
Run with: pytest tests/
"""
import pandas as pd
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from mrp_discrepancy_detector import compute_actual_qpu, detect_discrepancies


def test_compute_actual_qpu_basic():
    consumption = pd.DataFrame({
        "parent_sku": ["A", "A"],
        "component_sku": ["X", "X"],
        "actual_qty_consumed": [100, 100],
        "parent_units_serviced": [50, 50],
    })
    result = compute_actual_qpu(consumption)
    assert len(result) == 1
    assert result.loc[0, "actual_qpu"] == 2.0


def test_detect_discrepancies_flags_over_statement():
    bom = pd.DataFrame({
        "parent_sku": ["A"],
        "component_sku": ["X"],
        "description": ["widget"],
        "bom_qpu": [4.0],
        "unit_cost": [10.0],
    })
    actual = pd.DataFrame({
        "parent_sku": ["A"],
        "component_sku": ["X"],
        "total_qty_consumed": [200],
        "total_parent_units": [100],
        "actual_qpu": [2.0],
    })
    forecast = pd.DataFrame({
        "parent_sku": ["A"],
        "forecasted_units_next_period": [1000],
    })
    flagged = detect_discrepancies(bom, actual, forecast, threshold=0.15, min_units=50)
    assert len(flagged) == 1
    row = flagged.iloc[0]
    assert row["direction"].startswith("OVER")
    # gap = 4.0 - 2.0 = 2.0 per unit; exposure = 2.0 * 1000 * 10.0
    assert row["dollar_exposure"] == 20000.0


def test_detect_discrepancies_respects_min_units_guard():
    bom = pd.DataFrame({
        "parent_sku": ["A"],
        "component_sku": ["X"],
        "description": ["widget"],
        "bom_qpu": [4.0],
        "unit_cost": [10.0],
    })
    actual = pd.DataFrame({
        "parent_sku": ["A"],
        "component_sku": ["X"],
        "total_qty_consumed": [4],
        "total_parent_units": [2],  # below min_units guard
        "actual_qpu": [2.0],
    })
    forecast = pd.DataFrame({
        "parent_sku": ["A"],
        "forecasted_units_next_period": [1000],
    })
    flagged = detect_discrepancies(bom, actual, forecast, threshold=0.15, min_units=50)
    assert len(flagged) == 0
