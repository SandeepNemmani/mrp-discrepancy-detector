"""
Generate synthetic BOM, consumption, and forecast data for the MRP discrepancy detector.

This is SAMPLE DATA ONLY. No real company data is used anywhere in this repo.
The generator injects a known set of "planted" BOM errors so the detector's
output can be checked against ground truth.

Usage:
    python src/generate_sample_data.py
"""
import csv
import random
from pathlib import Path

random.seed(42)

OUT_DIR = Path(__file__).resolve().parent.parent / "sample_data"
OUT_DIR.mkdir(exist_ok=True)

N_PARENTS = 30          # finished service kits / repair assemblies
N_COMPONENTS_POOL = 60  # shared pool of component part numbers
PERIODS = 6             # months of consumption history

parents = [f"SVC-{1000 + i}" for i in range(N_PARENTS)]
components_pool = [f"CMP-{2000 + i}" for i in range(N_COMPONENTS_POOL)]

# Each parent draws 3-6 components from the pool, with a "true" (correct) qty-per-unit.
bom_rows = []
true_qpu = {}  # (parent, component) -> ground-truth actual usage rate
for parent in parents:
    n_comp = random.randint(3, 6)
    comps = random.sample(components_pool, n_comp)
    for comp in comps:
        qpu_true = round(random.choice([1, 1, 1, 2, 2, 3, 4]) * random.uniform(0.9, 1.1), 3)
        unit_cost = round(random.uniform(4, 180), 2)
        true_qpu[(parent, comp)] = qpu_true
        bom_rows.append({
            "parent_sku": parent,
            "component_sku": comp,
            "description": f"Component for {parent}",
            "bom_qpu": qpu_true,      # will be corrupted below for a subset
            "unit_cost": unit_cost,
        })

# Plant errors on ~18% of BOM lines: stale revision / UoM conversion bugs.
# Direction is mostly over-statement (the costly, common failure mode),
# with a few under-statements to show the detector isn't one-directional.
planted_errors = {}
error_candidates = random.sample(range(len(bom_rows)), max(1, int(len(bom_rows) * 0.18)))
for idx in error_candidates:
    row = bom_rows[idx]
    key = (row["parent_sku"], row["component_sku"])
    direction = random.choices(["over", "under"], weights=[0.8, 0.2])[0]
    factor = random.uniform(1.4, 2.2) if direction == "over" else random.uniform(0.4, 0.7)
    corrupted = round(row["bom_qpu"] * factor, 3)
    row["bom_qpu"] = corrupted
    planted_errors[key] = {"direction": direction, "factor": round(factor, 2)}

with open(OUT_DIR / "bom.csv", "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=["parent_sku", "component_sku", "description", "bom_qpu", "unit_cost"])
    writer.writeheader()
    writer.writerows(bom_rows)

# Actual consumption history: for each parent-component pair, simulate PERIODS
# months of "parent units serviced" and "component qty consumed", built from the
# ground-truth qpu plus normal noise (not the corrupted BOM figure).
consumption_rows = []
for (parent, comp), qpu in true_qpu.items():
    for period in range(1, PERIODS + 1):
        units_serviced = random.randint(40, 400)
        noise = random.uniform(0.92, 1.08)
        qty_consumed = max(0, round(units_serviced * qpu * noise))
        consumption_rows.append({
            "parent_sku": parent,
            "component_sku": comp,
            "period": f"2026-{period:02d}",
            "parent_units_serviced": units_serviced,
            "actual_qty_consumed": qty_consumed,
        })

with open(OUT_DIR / "actual_consumption.csv", "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=["parent_sku", "component_sku", "period",
                                            "parent_units_serviced", "actual_qty_consumed"])
    writer.writeheader()
    writer.writerows(consumption_rows)

# Forecasted demand per parent for next period (drives the $ exposure calc).
forecast_rows = [
    {"parent_sku": p, "forecasted_units_next_period": random.randint(150, 1200)}
    for p in parents
]
with open(OUT_DIR / "demand_forecast.csv", "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=["parent_sku", "forecasted_units_next_period"])
    writer.writeheader()
    writer.writerows(forecast_rows)

print(f"Wrote {len(bom_rows)} BOM lines, {len(consumption_rows)} consumption records, "
      f"{len(forecast_rows)} forecast rows to {OUT_DIR}")
print(f"Planted {len(planted_errors)} BOM errors for validation.")
