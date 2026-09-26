"""
MRP Discrepancy Detector
=========================

Finds Bill-of-Materials (BOM) lines whose recorded quantity-per-unit (QPU)
has drifted from what actual consumption history shows, and prices out the
dollar exposure that drift creates in next period's MRP-driven purchase
requirements.

Why this matters
----------------
MRP multiplies a BOM's stated quantity-per-unit by forecasted demand to
generate purchase requirements. A single stale BOM line (a superseded
revision, a unit-of-measure conversion error, a part substitution that
was never updated) gets multiplied across every unit forecasted, and the
error compounds silently until someone notices excess inventory sitting
on a shelf. In a production planning environment, this exact pattern was
identified across a set of BOM lines that were inflating MRP-driven
requirements by more than 50% on the affected parts, avoiding roughly
$1M a year in unnecessary purchase orders once corrected.

This script is a generalized, sample-data version of that detection
approach: it does not use any real company data.

Usage
-----
    python src/mrp_discrepancy_detector.py \
        --bom sample_data/bom.csv \
        --consumption sample_data/actual_consumption.csv \
        --forecast sample_data/demand_forecast.csv \
        --threshold 0.15 \
        --min-units 500 \
        --output-dir output
"""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # headless-safe backend
import matplotlib.pyplot as plt
import pandas as pd


def load_inputs(bom_path: str, consumption_path: str, forecast_path: str):
    bom = pd.read_csv(bom_path)
    consumption = pd.read_csv(consumption_path)
    forecast = pd.read_csv(forecast_path)
    return bom, consumption, forecast


def compute_actual_qpu(consumption: pd.DataFrame) -> pd.DataFrame:
    """Roll up multi-period consumption history into one actual QPU per
    parent/component pair, weighted by volume rather than averaged period
    to period (a handful of low-volume months should not dominate)."""
    grouped = (
        consumption.groupby(["parent_sku", "component_sku"])
        .agg(
            total_qty_consumed=("actual_qty_consumed", "sum"),
            total_parent_units=("parent_units_serviced", "sum"),
        )
        .reset_index()
    )
    grouped["actual_qpu"] = grouped["total_qty_consumed"] / grouped["total_parent_units"]
    return grouped


def detect_discrepancies(
    bom: pd.DataFrame,
    actual: pd.DataFrame,
    forecast: pd.DataFrame,
    threshold: float = 0.15,
    min_units: int = 500,
) -> pd.DataFrame:
    """Flag BOM lines whose stated QPU deviates from actual usage by more
    than `threshold` (as a fraction of actual), requiring at least
    `min_units` of historical parent volume so a flag reflects a real
    pattern rather than noise from a handful of transactions."""
    merged = bom.merge(actual, on=["parent_sku", "component_sku"], how="inner")
    merged = merged.merge(forecast, on="parent_sku", how="left")

    merged = merged[merged["total_parent_units"] >= min_units].copy()

    merged["deviation_pct"] = (merged["bom_qpu"] - merged["actual_qpu"]) / merged["actual_qpu"]
    merged["direction"] = merged["deviation_pct"].apply(
        lambda x: "OVER-STATED (over-procurement risk)" if x > 0 else "UNDER-STATED (stockout risk)"
    )

    flagged = merged[merged["deviation_pct"].abs() >= threshold].copy()

    # Dollar exposure: the quantity gap per unit, times forecasted volume,
    # times unit cost. For over-statements this is inventory you will buy
    # and not need; for under-statements it is unbudgeted emergency buys.
    flagged["qty_gap_per_unit"] = flagged["bom_qpu"] - flagged["actual_qpu"]
    flagged["forecasted_units_next_period"] = flagged["forecasted_units_next_period"].fillna(0)
    flagged["dollar_exposure"] = (
        flagged["qty_gap_per_unit"].abs()
        * flagged["forecasted_units_next_period"]
        * flagged["unit_cost"]
    )

    flagged = flagged.sort_values("dollar_exposure", ascending=False).reset_index(drop=True)
    return flagged[[
        "parent_sku", "component_sku", "description", "bom_qpu", "actual_qpu",
        "deviation_pct", "direction", "total_parent_units", "unit_cost",
        "forecasted_units_next_period", "dollar_exposure",
    ]]


def save_report(flagged: pd.DataFrame, output_dir: str) -> Path:
    out = Path(output_dir)
    out.mkdir(exist_ok=True, parents=True)
    csv_path = out / "discrepancy_report.csv"
    flagged.to_csv(csv_path, index=False)
    return csv_path


def save_chart(flagged: pd.DataFrame, output_dir: str, top_n: int = 10) -> Path:
    out = Path(output_dir)
    out.mkdir(exist_ok=True, parents=True)
    top = flagged.head(top_n).iloc[::-1]  # reverse so #1 plots on top
    labels = top["parent_sku"] + " / " + top["component_sku"]

    fig, ax = plt.subplots(figsize=(9, 6))
    colors = ["#c0392b" if d.startswith("OVER") else "#e67e22" for d in top["direction"]]
    ax.barh(labels, top["dollar_exposure"], color=colors)
    ax.set_xlabel("Annualized dollar exposure ($)")
    ax.set_title(f"Top {min(top_n, len(top))} BOM discrepancies by dollar exposure")
    fig.tight_layout()

    chart_path = out / "top_discrepancies.png"
    fig.savefig(chart_path, dpi=150)
    plt.close(fig)
    return chart_path


def main():
    parser = argparse.ArgumentParser(description="Detect BOM/MRP quantity-per-unit discrepancies.")
    parser.add_argument("--bom", default="sample_data/bom.csv")
    parser.add_argument("--consumption", default="sample_data/actual_consumption.csv")
    parser.add_argument("--forecast", default="sample_data/demand_forecast.csv")
    parser.add_argument("--threshold", type=float, default=0.15,
                         help="Minimum absolute deviation (as a fraction of actual QPU) to flag. Default 0.15.")
    parser.add_argument("--min-units", type=int, default=500,
                         help="Minimum historical parent-unit volume required before flagging. Default 500.")
    parser.add_argument("--output-dir", default="output")
    args = parser.parse_args()

    bom, consumption, forecast = load_inputs(args.bom, args.consumption, args.forecast)
    actual = compute_actual_qpu(consumption)
    flagged = detect_discrepancies(bom, actual, forecast, args.threshold, args.min_units)

    csv_path = save_report(flagged, args.output_dir)
    chart_path = save_chart(flagged, args.output_dir) if len(flagged) else None

    total_exposure = flagged["dollar_exposure"].sum()
    over_count = (flagged["direction"].str.startswith("OVER")).sum()
    under_count = len(flagged) - over_count

    print(f"\nScanned {len(bom)} BOM lines against {consumption['parent_sku'].nunique()} parent SKUs "
          f"of consumption history.")
    print(f"Flagged {len(flagged)} discrepancies ({over_count} over-stated, {under_count} under-stated) "
          f"at a {args.threshold:.0%} threshold with >= {args.min_units} units of history.")
    print(f"Total annualized dollar exposure across flagged lines: ${total_exposure:,.0f}")
    print(f"\nTop 5 by exposure:")
    with pd.option_context("display.max_columns", None, "display.width", 140):
        print(flagged.head(5)[["parent_sku", "component_sku", "bom_qpu", "actual_qpu",
                                "deviation_pct", "direction", "dollar_exposure"]]
              .to_string(index=False))

    print(f"\nFull report:  {csv_path}")
    if chart_path:
        print(f"Chart:        {chart_path}")


if __name__ == "__main__":
    main()
