"""
MRP Demand Audit
================

Finds the master-data errors that inflate (or starve) MRP demand, names the
root cause for each part, recommends the fix, and prices the impact.

Three checks, each matching a root cause found in a real production-planning
audit:

  1. OPTION CODE CHECK  Option-coded vehicle demand must never exceed the build
                        plan it came from. If a one-per-vehicle part family adds
                        up to more vehicles than the market (or the world) will
                        build, a part is carrying an option code it shouldn't,
                        e.g. a KR part tagged with 3-phase charging as well as
                        the 1-phase that KR uses.
  2. YIELD CHECK        Ending inventory = on-hand + in-transit - actual
                        consumption, tracked week over week. If inventory keeps
                        climbing while consumption is flat, the BOM yield buffer
                        is bigger than the scrap seen at the workstation.
                        Recommended yield = observed scrap rate, rounded up to
                        the next whole percent.
  3. QPV CHECK          BOM quantity-per-vehicle is summed across every
                        workstation the part is tagged to. If actual good usage
                        per vehicle built is well below that, and some tagged
                        stations pull nothing, those tags are wrong.

Usage:
    python src/mrp_audit.py                # reads sample_data/, writes output/
    python src/mrp_audit.py --data-dir my_data --output-dir my_output
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker
import pandas as pd

WEEKS_PER_YEAR = 52
YIELD_STEP = 0.01          # recommended yield = scrap rounded up to the next 1%
YIELD_TOLERANCE = 0.05     # flag if BOM yield is off by 5 points or more
QPV_TOLERANCE = 0.15       # flag if BOM QPV is 15%+ above/below actual
COVERAGE_TOLERANCE = 0.01  # option-coded demand may not exceed build plan by >1%


# --------------------------------------------------------------------------- load
def load(data_dir: str | Path) -> dict[str, pd.DataFrame]:
    d = Path(data_dir)
    names = ["build_plan", "market_rules", "parts", "bom_workstations",
             "mrp_vehicle_demand", "inventory_weekly", "station_consumption"]
    return {n: pd.read_csv(d / f"{n}.csv") for n in names}


# --------------------------------------------------------------- 1. option codes
def demand_validation(data: dict) -> pd.DataFrame:
    """Coverage test: option-coded vehicle demand for a one-per-vehicle family,
    divided by the build plan, by market and week. Anything over 100% is
    physically impossible."""
    parts, mrp, bp = data["parts"], data["mrp_vehicle_demand"], data["build_plan"]
    coded = parts[parts["market"] != "ALL"][["part", "family"]]
    d = mrp.merge(coded, on="part")
    by_mkt = d.groupby(["family", "week", "market"])["vehicle_demand"].sum().reset_index()
    by_mkt = by_mkt.merge(bp, on=["week", "market"])
    by_mkt["coverage"] = by_mkt["vehicle_demand"] / by_mkt["planned_builds"]
    return by_mkt


def check_option_codes(data: dict) -> pd.DataFrame:
    parts, rules, mrp = data["parts"], data["market_rules"], data["mrp_vehicle_demand"]
    valid_phase = dict(zip(rules["market"], rules["charging_phase"]))
    weeks = mrp["week"].nunique()
    rows = []
    for _, p in parts[parts["market"] != "ALL"].iterrows():
        for key in p["option_codes"].split("|"):
            market, phase = key.split("+")
            if valid_phase.get(market) != phase:
                phantom = mrp[(mrp["part"] == p["part"]) & (mrp["option_key"] == key)]["vehicle_demand"].sum()
                rows.append({
                    "part": p["part"], "issue": "OPTION_CODE",
                    "finding": f"{market} uses {valid_phase.get(market)} but part also carries {phase}",
                    "as_is": p["option_codes"],
                    "recommended": "|".join(k for k in p["option_codes"].split("|") if k != key),
                    "fix": f"Remove option code {key}",
                    "phantom_vehicles_per_week": phantom / weeks,
                })
    return pd.DataFrame(rows)


# ------------------------------------------------------------------------ 3. QPV
def check_qpv(data: dict) -> pd.DataFrame:
    bom, inv, st = data["bom_workstations"], data["inventory_weekly"], data["station_consumption"]
    bom_qpv = bom.groupby("part")["qpv"].sum()
    use = inv.groupby("part")[["actual_consumption", "scrap_qty", "vehicles_built"]].sum()
    actual_qpv = (use["actual_consumption"] - use["scrap_qty"]) / use["vehicles_built"]
    rows = []
    for part, bq in bom_qpv.items():
        aq = actual_qpv[part]
        if abs(bq - aq) / aq < QPV_TOLERANCE:
            continue
        tags = bom[bom["part"] == part].merge(st, on=["part", "workstation"])
        live = tags[tags["qty_issued_13wk"] > 0]
        dead = tags[tags["qty_issued_13wk"] == 0]
        rec = live["qpv"].sum() if len(live) else round(aq)
        rows.append({
            "part": part, "issue": "QPV",
            "finding": f"BOM QPV {bq:g} across {len(tags)} stations; actual good usage {aq:.2f}/vehicle",
            "as_is": bq, "recommended": rec,
            "fix": ("Remove station tag(s) " + ", ".join(dead["workstation"])) if len(dead)
                   else f"Correct QPV to {rec}",
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------- 2. yield
def inventory_trend(data: dict) -> pd.DataFrame:
    """Sandy's formula: ending inventory = on-hand + in-transit - actual
    consumption, compared week over week."""
    inv = data["inventory_weekly"].copy()
    inv["ending_calc"] = inv["on_hand"] + inv["in_transit"] - inv["actual_consumption"]
    inv["wow_change"] = inv.groupby("part")["ending_calc"].diff()
    return inv


def check_yield(data: dict) -> pd.DataFrame:
    parts = data["parts"].set_index("part")
    inv = inventory_trend(data)
    g = inv.groupby("part")
    scrap = g["scrap_qty"].sum() / (g["actual_consumption"].sum() - g["scrap_qty"].sum())
    growth = g["wow_change"].mean() / g["actual_consumption"].mean()   # weekly build-up as % of usage
    rows = []
    for part, rate in scrap.items():
        bom_y = parts.loc[part, "bom_yield"]
        rec = math.ceil(round(rate / YIELD_STEP, 6)) * YIELD_STEP
        if abs(bom_y - rec) < YIELD_TOLERANCE - 1e-9:
            continue
        direction = "over-buffered" if bom_y > rec else "under-buffered"
        rows.append({
            "part": part, "issue": "YIELD",
            "finding": (f"BOM yield {bom_y:.0%} vs scrap {rate:.1%} ({direction}); "
                        f"inventory moving {growth[part]:+.0%} of weekly usage per week"),
            "as_is": bom_y, "recommended": round(rec, 2),
            "fix": f"NPI to update BOM yield {bom_y:.0%} -> {rec:.0%}",
        })
    return pd.DataFrame(rows)


# ----------------------------------------------------------------- dollar impact
def price_impact(data: dict, findings: pd.DataFrame) -> pd.DataFrame:
    """Weekly MRP requirement as-is vs corrected, per part. Corrections are
    applied in order (option code -> QPV -> yield) so each root cause gets its
    own slice and the slices add up to the total."""
    parts = data["parts"].set_index("part")
    mrp, bom = data["mrp_vehicle_demand"], data["bom_workstations"]
    weeks = mrp["week"].nunique()
    veh = mrp.groupby("part")["vehicle_demand"].sum() / weeks
    qpv = bom.groupby("part")["qpv"].sum()
    out = []
    for part in findings["part"].unique():
        f = findings[findings["part"] == part].set_index("issue")
        cost = parts.loc[part, "unit_cost"]
        v0, q0, y0 = veh[part], qpv[part], parts.loc[part, "bom_yield"]
        v1 = v0 - (f.loc["OPTION_CODE", "phantom_vehicles_per_week"] if "OPTION_CODE" in f.index else 0)
        q1 = float(f.loc["QPV", "recommended"]) if "QPV" in f.index else q0
        y1 = float(f.loc["YIELD", "recommended"]) if "YIELD" in f.index else y0
        steps = [("AS_IS", v0, q0, y0), ("OPTION_CODE", v1, q0, y0), ("QPV", v1, q1, y0), ("YIELD", v1, q1, y1)]
        units = [v * q * (1 + y) for _, v, q, y in steps]
        for i in range(1, 4):
            if steps[i][0] in f.index:
                delta = units[i - 1] - units[i]
                out.append({"part": part, "issue": steps[i][0],
                            "weekly_units_as_is": units[0], "weekly_units_corrected": units[-1],
                            "excess_units_per_week": delta,
                            "annualized_excess_usd": delta * cost * WEEKS_PER_YEAR})
    return pd.DataFrame(out)


# ----------------------------------------------------------------------- charts
def charts(data, findings, impact, out: Path):
    out.mkdir(parents=True, exist_ok=True)
    colors = {"OPTION_CODE": "#2e86c1", "YIELD": "#e67e22", "QPV": "#c0392b"}

    # 1. demand validation
    cov = demand_validation(data).groupby("market")["coverage"].mean().sort_values()
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.barh(cov.index, cov.values * 100, color=["#c0392b" if c > 1 + COVERAGE_TOLERANCE else "#7f8c8d" for c in cov])
    ax.axvline(100, color="black", lw=1, ls="--")
    ax.set_xlabel("Option-coded charge-inlet demand as % of build plan")
    ax.set_title("Demand validation: anything past 100% can't be built")
    fig.tight_layout(); fig.savefig(out / "1_demand_validation.png", dpi=150); plt.close(fig)

    # 2. inventory trend for the worst over-buffered yield part vs a clean part
    inv = inventory_trend(data)
    y = impact[impact["issue"] == "YIELD"].sort_values("annualized_excess_usd", ascending=False)
    if len(y):
        bad = y.iloc[0]["part"]
        clean = next(p for p in data["parts"]["part"] if p not in set(findings["part"]) and p.startswith("GEN"))
        fig, ax = plt.subplots(figsize=(8, 4.5))
        for p, c in [(bad, "#e67e22"), (clean, "#7f8c8d")]:
            s = inv[inv["part"] == p]
            ax.plot(s["week"].str[-3:], s["ending_calc"] / s["actual_consumption"].mean(), marker="o", color=c,
                    label=f"{p} ({'yield error' if p == bad else 'clean'})")
        ax.set_ylabel("Ending inventory, in weeks of usage")
        ax.set_title("On-hand + in-transit - consumption, week over week")
        ax.legend(); fig.tight_layout(); fig.savefig(out / "2_inventory_trend.png", dpi=150); plt.close(fig)

    # 3. annualized excess by part, stacked by root cause
    pv = impact.pivot_table(index="part", columns="issue", values="annualized_excess_usd", aggfunc="sum").fillna(0)
    pv = pv.loc[pv.sum(axis=1).sort_values().index]
    fig, ax = plt.subplots(figsize=(8, 5.5))
    left = pd.Series(0.0, index=pv.index)
    for issue in ["OPTION_CODE", "QPV", "YIELD"]:
        if issue in pv:
            ax.barh(pv.index, pv[issue], left=left, color=colors[issue], label={"OPTION_CODE": "Option code", "QPV": "QPV", "YIELD": "Yield"}[issue])
            left += pv[issue]
    ax.axvline(0, color="black", lw=0.8)
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda x, _: f"${x/1e6:.1f}M" if abs(x) >= 1e6 else f"${x/1e3:.0f}K"))
    ax.set_xlabel("Annualized excess MRP requirement ($, 13-week run rate x 52)")
    ax.set_title("Where the inflation comes from, by part and root cause")
    ax.legend(); fig.tight_layout(); fig.savefig(out / "3_impact_by_root_cause.png", dpi=150); plt.close(fig)


# ------------------------------------------------------------------------- main
def run(data_dir="sample_data", output_dir="output", quiet=False):
    data = load(data_dir)
    findings = pd.concat([check_option_codes(data), check_qpv(data), check_yield(data)], ignore_index=True)
    impact = price_impact(data, findings)
    report = findings.drop(columns=["phantom_vehicles_per_week"], errors="ignore").merge(
        impact[["part", "issue", "excess_units_per_week", "annualized_excess_usd"]], on=["part", "issue"])
    report = report.sort_values("annualized_excess_usd", ascending=False).reset_index(drop=True)

    out = Path(output_dir); out.mkdir(parents=True, exist_ok=True)
    report.to_csv(out / "audit_findings.csv", index=False)
    charts(data, findings, impact, out)

    if not quiet:
        cov = demand_validation(data)
        g = cov.groupby("week")[["vehicle_demand", "planned_builds"]].sum()
        worst = cov.groupby("market")["coverage"].mean().sort_values(ascending=False)
        per_part = impact.drop_duplicates("part")
        as_is = (per_part["weekly_units_as_is"] * per_part["part"].map(data["parts"].set_index("part")["unit_cost"])).sum()
        fixed = (per_part["weekly_units_corrected"] * per_part["part"].map(data["parts"].set_index("part")["unit_cost"])).sum()

        print("\nDEMAND VALIDATION (charge-inlet family, one per vehicle)")
        print(f"  Option-coded demand vs global build plan: {g['vehicle_demand'].sum():,} vs "
              f"{g['planned_builds'].sum():,} ({g['vehicle_demand'].sum() / g['planned_builds'].sum():.0%})")
        print("  Markets over 100%: " + ", ".join(f"{m} {c:.0%}" for m, c in worst.items() if c > 1 + COVERAGE_TOLERANCE))
        print(f"\nFINDINGS: {len(report)} across {report['part'].nunique()} parts  "
              + "  ".join(f"{k}: {v}" for k, v in report['issue'].value_counts().items()))
        with pd.option_context("display.width", 200, "display.max_colwidth", 70):
            print(report[["part", "issue", "as_is", "recommended", "fix", "annualized_excess_usd"]]
                  .round({"annualized_excess_usd": 0}).to_string(index=False))
        print(f"\nOn the affected parts, MRP was asking for {as_is / fixed - 1:.0%} more (by value) than the "
              f"corrected BOM needs.")
        print(f"Annualized excess requirement: ${report['annualized_excess_usd'].clip(lower=0).sum():,.0f} over-stated, "
              f"${-report['annualized_excess_usd'].clip(upper=0).sum():,.0f} under-stated (stockout risk).")
        print(f"\nWrote {out / 'audit_findings.csv'} and 3 charts to {out}/")
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Audit MRP master data for option-code, yield and QPV errors.")
    ap.add_argument("--data-dir", default="sample_data")
    ap.add_argument("--output-dir", default="output")
    a = ap.parse_args()
    run(a.data_dir, a.output_dir)
