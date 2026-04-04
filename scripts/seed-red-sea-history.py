#!/usr/bin/env python3
import json
import os
from datetime import datetime, date, timedelta
from pathlib import Path

# --- Configuration ---
DATA_FILE = Path("data/red-sea.json")
HISTORY_DIR = Path("data/red-sea-history")
WCI_TIMELINE = [
    ("2023-11-19", 1380),
    ("2023-12-15", 2100),
    ("2024-01-15", 4200),
    ("2024-02-15", 5800),
    ("2024-03-15", 5200),
    ("2024-06-01", 4800),
    ("2024-09-01", 3200),
    ("2024-12-01", 2400),
    ("2025-03-01", 2800),
    ("2025-06-01", 3100),
    ("2025-09-01", 3600),
    ("2026-01-01", 3900),
    ("2026-04-04", 4200),
]

def parse_date(d_str):
    return datetime.strptime(d_str, "%Y-%m-%d").date()

def interpolate_wci(target_date):
    # Sort timeline just in case
    sorted_timeline = sorted([(parse_date(d), v) for d, v in WCI_TIMELINE])
    
    if target_date <= sorted_timeline[0][0]:
        return sorted_timeline[0][1]
    if target_date >= sorted_timeline[-1][0]:
        return sorted_timeline[-1][1]
    
    for i in range(len(sorted_timeline) - 1):
        d1, v1 = sorted_timeline[i]
        d2, v2 = sorted_timeline[i+1]
        if d1 <= target_date <= d2:
            days_diff = (d2 - d1).days
            target_diff = (target_date - d1).days
            return v1 + (v2 - v1) * (target_diff / days_diff)
    return sorted_timeline[-1][1]

def main():
    if not DATA_FILE.exists():
        print(f"Error: {DATA_FILE} not found")
        return

    with open(DATA_FILE, "r") as f:
        data = json.load(f)

    HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Calculate baseline sensitivity for each food
    # Baseline is "today" (Apr 2026 values in red-sea.json)
    # WCI today is 4200
    wci_today = 4200
    wci_base = 1380
    wci_chg_today = (wci_today - wci_base) / wci_base * 100
    
    # mean(baseline_driver_pcts) as defined in prompt
    freight_today = wci_chg_today
    insurance_today = 56.0
    rerouting_today = wci_chg_today * 0.6
    mean_baseline_driver_pct = (freight_today + insurance_today + rerouting_today) / 3
    
    food_sensitivities = {}
    for food in data["foods"]:
        # sensitivity = baseline_exposure / mean(baseline_driver_pcts)
        food_sensitivities[food["id"]] = food["crisis_exposure_pct"] / mean_baseline_driver_pct

    # 2. Generate snapshots
    start_date = parse_date("2023-11-19")
    end_date = parse_date("2026-04-01")
    
    snapshot_dates = []
    # Monthly on the 1st
    curr = date(2023, 12, 1)
    while curr <= end_date:
        snapshot_dates.append(curr)
        # Move to 1st of next month
        if curr.month == 12:
            curr = date(curr.year + 1, 1, 1)
        else:
            curr = date(curr.year, curr.month + 1, 1)

    for d in snapshot_dates:
        wci = interpolate_wci(d)
        wci_chg_pct = (wci - wci_base) / wci_base * 100
        
        driver_pcts = {
            "freight": wci_chg_pct,
            "insurance": 56.0,
            "rerouting": wci_chg_pct * 0.6
        }
        
        snapshot_foods = []
        for food in data["foods"]:
            # weighted_input = sum(driver.weight * driver_pct[driver.category]) / sum(driver.weight)
            weight_sum = sum(d["weight"] for d in food["drivers"])
            weighted_input = sum(d["weight"] * driver_pcts.get(d["category"], 0) for d in food["drivers"]) / weight_sum
            
            # raw = weighted_input * sensitivity
            sensitivity = food_sensitivities[food["id"]]
            raw = weighted_input * sensitivity
            
            # exposure = max(1, min(100 - local_cost_floor_pct, round(raw)))
            floor_cap = 100 - food["local_cost_floor_pct"]
            exposure = max(1, min(floor_cap, round(raw)))
            
            snapshot_foods.append({
                "id": food["id"],
                "crisis_exposure_pct": int(exposure)
            })
            
        snapshot = {
            "date": d.isoformat(),
            "sources": {
                "drewry_wci_usd_40ft": round(wci, 2)
            },
            "foods": snapshot_foods
        }
        
        filename = HISTORY_DIR / f"{d.isoformat()}.json"
        with open(filename, "w") as f:
            json.dump(snapshot, f, indent=2)
        print(f"Generated {filename}")

    # 3. Generate index.json
    index_file = HISTORY_DIR / "index.json"
    with open(index_file, "w") as f:
        json.dump([d.isoformat() for d in snapshot_dates], f, indent=2)
    print(f"Generated {index_file}")

if __name__ == "__main__":
    main()
