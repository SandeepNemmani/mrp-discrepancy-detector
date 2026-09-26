"""
Generate synthetic planning data for the MRP demand audit.

SAMPLE DATA ONLY. Markets, option codes, part numbers, workstations, costs and
volumes are all invented. The market-to-charging-phase mapping is illustrative.

The generator plants three kinds of BOM/master-data error, the same three root
causes found in a real production-planning audit:

  1. OPTION CODE  - a market-coded part also carries the charging-phase code
                    that its market doesn't use (e.g. a KR part tagged 3PH as
                    well as 1PH), so MRP explodes demand for both.
  2. YIELD        - the BOM yield buffer is set higher (or lower) than the
                    scrap actually seen at the workstation.
  3. QPV          - a part is tagged at extra workstations, each carrying the
                    full quantity, so quantity-per-vehicle is summed too high.

Every planted error is written to sample_data/answer_key.csv so the audit's
output can be checked against ground truth.

Usage:
    python src/generate_sample_data.py
"""
import csv
import math
import random
from pathlib import Path

random.seed(7)
OUT = Path(__file__).resolve().parent.parent / "sample_data"
OUT.mkdir(exist_ok=True)

WEEKS = [f"2026-W{w:02d}" for w in range(1, 14)]   # 13 weeks = one quarter

# market -> (region, charging phase the market uses, share of global builds)
MARKETS = {
    "US": ("NA", "1PH", 0.30), "CA": ("NA", "1PH", 0.06),
    "DE": ("EMEA", "3PH", 0.12), "NL": ("EMEA", "3PH", 0.06), "NO": ("EMEA", "3PH", 0.05),
    "CN": ("APAC", "3PH", 0.20), "AU": ("APAC", "3PH", 0.06),
    "KR": ("APAC", "1PH", 0.09), "JP": ("APAC", "1PH", 0.06),
}
# Global planning take rate of each charging option (share of all builds).
PHASE_TAKE_RATE = {
    ph: round(sum(s for _, p, s in MARKETS.values() if p == ph), 2) for ph in ("1PH", "3PH")
}
STATIONS = [f"WS-{n:03d}" for n in range(100, 400, 10)]

# ---------------------------------------------------------------- build plan
build_rows = []
weekly_global = {}
for wk in WEEKS:
    total = random.randint(900, 1100)             # one program, ~1k vehicles a week
    weekly_global[wk] = total
    for m, (region, phase, share) in MARKETS.items():
        build_rows.append({"week": wk, "market": m, "region": region,
                           "planned_builds": round(total * share)})
builds = {(r["week"], r["market"]): r["planned_builds"] for r in build_rows}

# ---------------------------------------------------------------- parts
parts, answer_key = [], []

# (a) market-coded charging parts: one per vehicle, one part per market
for m, (region, phase, _) in MARKETS.items():
    parts.append({"part": f"CHG-{m}", "description": f"Charge inlet assembly, {m}",
                  "family": "CHARGE_INLET", "market": m,
                  "option_codes": f"{m}+{phase}", "true_qpv": 1, "unit_cost": round(random.uniform(35, 70), 2),
                  "true_scrap": round(random.uniform(0.01, 0.03), 3)})

# (b) general parts: fitted to every vehicle
descs = ["Harness clip", "Seal, door", "Bracket, trim", "Fastener, M6", "Grommet", "Foam pad",
         "Label, VIN", "Clip, wiring", "Sensor mount", "Hose clamp", "Bushing", "Washer, M8",
         "Cover, access", "Retainer", "Spacer", "Tape, NVH", "Nut, flange", "Bolt, M10",
         "Shield, heat", "Duct, air", "Cap, end", "Plug, body", "Strap, cable", "Pin, locating",
         "Rivet", "Adhesive cartridge", "Gasket", "Insulator", "Bezel", "Trim panel clip"]
for i, d in enumerate(descs):
    parts.append({"part": f"GEN-{3000 + i}", "description": d, "family": "GENERAL", "market": "ALL",
                  "option_codes": "ALL", "true_qpv": random.choice([1, 1, 2, 2, 4, 6, 8]),
                  "unit_cost": round(random.uniform(0.05, 6), 2),
                  "true_scrap": round(random.uniform(0.01, 0.09), 3)})

# every part starts with a correctly set BOM: yield buffer = observed scrap
# rounded up to the next whole percent, one workstation carrying the full QPV
for p in parts:
    p["bom_yield"] = math.ceil(p["true_scrap"] * 100) / 100
    p["stations"] = [(random.choice(STATIONS), p["true_qpv"])]
    p["bom_codes"] = p["option_codes"]

# ---- plant error 1: OPTION CODE (3 charging parts get the other phase too)
chg = [p for p in parts if p["family"] == "CHARGE_INLET"]
kr = next(p for p in chg if p["market"] == "KR")          # the KR + 3PH case
for p in [kr] + random.sample([p for p in chg if p is not kr], 2):
    right = p["option_codes"].split("+")[1]
    wrong = "3PH" if right == "1PH" else "1PH"
    p["bom_codes"] = f"{p['market']}+1PH|{p['market']}+3PH"
    answer_key.append({"part": p["part"], "issue": "OPTION_CODE",
                       "as_is": p["bom_codes"], "correct": p["option_codes"],
                       "note": f"remove {p['market']}+{wrong}"})

gen = [p for p in parts if p["family"] == "GENERAL"]
picked = random.sample(gen, 11)
yield_parts, qpv_parts = picked[:6], picked[5:]      # one part gets both errors

# ---- plant error 2: YIELD (5 inflated, 1 understated)
for i, p in enumerate(yield_parts):
    if i < 5:
        p["bom_yield"] = round(p["bom_yield"] + random.choice([0.08, 0.10, 0.12, 0.15]), 2)
    else:
        p["true_scrap"] = 0.085
        p["bom_yield"] = 0.02
    answer_key.append({"part": p["part"], "issue": "YIELD", "as_is": p["bom_yield"],
                       "correct": math.ceil(p["true_scrap"] * 100) / 100,
                       "note": "over" if i < 5 else "under"})

# ---- plant error 3: QPV (part tagged at 1-2 extra stations, full qty each)
for p in qpv_parts:
    extra = random.sample([s for s in STATIONS if s != p["stations"][0][0]], random.choice([1, 1, 1, 2]))
    p["stations"] += [(s, p["true_qpv"]) for s in extra]
    answer_key.append({"part": p["part"], "issue": "QPV",
                       "as_is": sum(q for _, q in p["stations"]), "correct": p["true_qpv"],
                       "note": f"true station {p['stations'][0][0]}"})

# ---------------------------------------------------------------- write masters
with open(OUT / "build_plan.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["week", "market", "region", "planned_builds"])
    w.writeheader(); w.writerows(build_rows)

with open(OUT / "market_rules.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["market", "region", "charging_phase"])
    for m, (r, ph, _) in MARKETS.items():
        w.writerow([m, r, ph])

with open(OUT / "parts.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["part", "description", "family", "market", "option_codes", "unit_cost", "bom_yield"])
    for p in parts:
        w.writerow([p["part"], p["description"], p["family"], p["market"], p["bom_codes"],
                    p["unit_cost"], p["bom_yield"]])

with open(OUT / "bom_workstations.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["part", "workstation", "qpv"])
    for p in parts:
        for s, q in p["stations"]:
            w.writerow([p["part"], s, q])

# ---------------------------------------------------------------- MRP demand
# Option-coded vehicle demand, one row per part x option key x week.
# The valid key gets the market's builds (its phase take rate within the market
# is 100%). An invalid key has no market-level rate, so it falls back to the
# global take rate of that phase -- phantom demand on top of real demand.
def key_demand(wk, market, phase):
    valid = MARKETS[market][1] == phase
    rate = 1.0 if valid else PHASE_TAKE_RATE[phase]
    return round(builds[(wk, market)] * rate), (1.0 if valid else rate)

mrp_rows = []
for wk in WEEKS:
    for p in parts:
        for key in p["bom_codes"].split("|"):
            if key == "ALL":
                mrp_rows.append({"week": wk, "part": p["part"], "option_key": "ALL", "market": "ALL",
                                 "take_rate": 1.0, "vehicle_demand": weekly_global[wk]})
            else:
                m, ph = key.split("+")
                d, tr = key_demand(wk, m, ph)
                mrp_rows.append({"week": wk, "part": p["part"], "option_key": key, "market": m,
                                 "take_rate": tr, "vehicle_demand": d})
with open(OUT / "mrp_vehicle_demand.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["week", "part", "option_key", "market", "take_rate", "vehicle_demand"])
    w.writeheader(); w.writerows(mrp_rows)

# ---------------------------------------------------------------- calibration
# Scale unit costs on the QPV-error parts so that, across all affected parts,
# as-is MRP requirement is TARGET_INFLATION above the corrected requirement
# (by value). Then scale every unit cost by one common factor so the annualized
# over-procurement lands on TARGET_ANNUAL_USD (a uniform scale leaves the 57%
# untouched). Both targets match the real audit's result.
TARGET_INFLATION = 0.57
TARGET_ANNUAL_USD = 8_400_000
key_by = {(a["part"], a["issue"]): a for a in answer_key}
def weekly_value(p, corrected):
    rows = [r for r in mrp_rows if r["part"] == p["part"]]
    if corrected:
        rows = [r for r in rows if r["option_key"] in ("ALL", p["option_codes"])]
    veh = sum(r["vehicle_demand"] for r in rows) / len(WEEKS)
    qpv = p["true_qpv"] if corrected else sum(q for _, q in p["stations"])
    y = key_by[(p["part"], "YIELD")]["correct"] if corrected and (p["part"], "YIELD") in key_by else p["bom_yield"]
    return veh * qpv * (1 + y) * p["unit_cost"]
affected = [p for p in parts if any(k[0] == p["part"] for k in key_by)]
q_grp = [p for p in affected if (p["part"], "QPV") in key_by]
o_grp = [p for p in affected if p not in q_grp]
A_o, C_o = sum(weekly_value(p, False) for p in o_grp), sum(weekly_value(p, True) for p in o_grp)
A_q, C_q = sum(weekly_value(p, False) for p in q_grp), sum(weekly_value(p, True) for p in q_grp)
k = ((1 + TARGET_INFLATION) * C_o - A_o) / (A_q - (1 + TARGET_INFLATION) * C_q)
assert k > 0, "target not reachable with these planted errors"
for p in q_grp:
    p["unit_cost"] = round(p["unit_cost"] * k, 4)
with open(OUT / "parts.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["part", "description", "family", "market", "option_codes", "unit_cost", "bom_yield"])
    for p in parts:
        w.writerow([p["part"], p["description"], p["family"], p["market"], p["bom_codes"],
                    p["unit_cost"], p["bom_yield"]])

# ---------------------------------------------------------------- inventory ledger
# Supply arrives per MRP (BOM qpv x (1 + BOM yield) x MRP vehicle demand, one week
# in transit). Consumption follows reality (true qpv x (1 + true scrap) x vehicles
# actually built). Any BOM error shows up as inventory that keeps climbing.
inv_rows = []
for p in parts:
    bom_qpv = sum(q for _, q in p["stations"])
    on_hand = None
    for i, wk in enumerate(WEEKS):
        mrp_veh = sum(r["vehicle_demand"] for r in mrp_rows if r["week"] == wk and r["part"] == p["part"])
        true_veh = weekly_global[wk] if p["market"] == "ALL" else builds[(wk, p["market"])]
        built = round(true_veh * random.uniform(0.97, 1.0))
        good_use = built * p["true_qpv"]
        scrap = round(good_use * p["true_scrap"] * random.uniform(0.85, 1.15))
        consumption = good_use + scrap
        in_transit = round(mrp_veh * bom_qpv * (1 + p["bom_yield"]))
        if on_hand is None:
            on_hand = round(consumption * 1.5)          # ~1.5 weeks opening cover
        ending = on_hand + in_transit - consumption
        inv_rows.append({"week": wk, "part": p["part"], "on_hand": on_hand, "in_transit": in_transit,
                         "actual_consumption": consumption, "scrap_qty": scrap,
                         "vehicles_built": built, "ending_inventory": ending})
        on_hand = max(ending, 0)
with open(OUT / "inventory_weekly.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(inv_rows[0].keys()))
    w.writeheader(); w.writerows(inv_rows)

# Station-level issues over the quarter (material actually pulled at each station
# the BOM tags). Stations tagged in error show nothing pulled.
with open(OUT / "station_consumption.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["part", "workstation", "qty_issued_13wk"])
    for p in parts:
        good = sum(r["actual_consumption"] - r["scrap_qty"] for r in inv_rows if r["part"] == p["part"])
        for j, (s, _) in enumerate(p["stations"]):
            w.writerow([p["part"], s, good if j == 0 else 0])

with open(OUT / "answer_key.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["part", "issue", "as_is", "correct", "note"])
    w.writeheader(); w.writerows(answer_key)

# second calibration pass: run the audit on the data just written, then scale
# all unit costs so annualized over-procurement equals TARGET_ANNUAL_USD
import sys, tempfile
sys.path.insert(0, str(Path(__file__).resolve().parent))
import mrp_audit
with tempfile.TemporaryDirectory() as tmp:
    rep = mrp_audit.run(OUT, tmp, quiet=True)
scale = TARGET_ANNUAL_USD / rep["annualized_excess_usd"].clip(lower=0).sum()
for p in parts:
    p["unit_cost"] = round(p["unit_cost"] * scale, 4)
with open(OUT / "parts.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["part", "description", "family", "market", "option_codes", "unit_cost", "bom_yield"])
    for p in parts:
        w.writerow([p["part"], p["description"], p["family"], p["market"], p["bom_codes"],
                    p["unit_cost"], p["bom_yield"]])

print(f"{len(parts)} parts, {len(WEEKS)} weeks, {len(MARKETS)} markets. "
      f"Planted {len(answer_key)} errors: "
      + ", ".join(f"{k} {sum(a['issue'] == k for a in answer_key)}" for k in ("OPTION_CODE", "YIELD", "QPV")))
