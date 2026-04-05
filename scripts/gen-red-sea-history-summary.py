#!/usr/bin/env python3
import json
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
HISTORY_DIR = REPO_ROOT / "data" / "red-sea-history"
SUMMARY_FILE = REPO_ROOT / "data" / "red-sea-history-summary.json"

def main():
    index_file = HISTORY_DIR / "index.json"
    if not index_file.exists():
        print("Error: data/red-sea-history/index.json not found")
        return

    with open(index_file, "r") as f:
        dates = json.load(f)

    # Initial pre-crisis baseline (Nov 2023) is 0% for everyone
    CRISIS_START = "2023-11-19"
    all_dates = [CRISIS_START] + dates
    
    # Map food_id -> list of percentages
    history_map = {}

    # Initial zero
    # We need to know all food IDs. Let's get them from the first snapshot or red-sea.json
    red_sea_json = REPO_ROOT / "data" / "red-sea.json"
    with open(red_sea_json, "r") as f:
        master_data = json.load(f)
        food_ids = [f["id"] for f in master_data["foods"]]

    for fid in food_ids:
        history_map[fid] = [0]

    for d in dates:
        snap_file = HISTORY_DIR / f"{d}.json"
        with open(snap_file, "r") as f:
            snap = json.load(f)
            # snap["foods"] is a list of {id, crisis_exposure_pct}
            snap_foods = {f["id"]: f["crisis_exposure_pct"] for f in snap["foods"]}
            for fid in food_ids:
                val = snap_foods.get(fid, 0)
                history_map[fid].append(val)

    summary = {
        "dates": all_dates,
        "history": history_map
    }

    with open(SUMMARY_FILE, "w") as f:
        json.dump(summary, f)

    print(f"Generated {SUMMARY_FILE} with {len(all_dates)} data points for {len(food_ids)} foods.")

if __name__ == "__main__":
    main()
