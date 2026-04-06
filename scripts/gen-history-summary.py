#!/usr/bin/env python3
"""
Regenerate data/history-summary.json from data/history/*.json snapshots.
Run after fetch-data.py adds a new daily snapshot.

Output schema:
  {
    "dates":         ["2026-02-28", ...],   # crisis-start baseline + all snapshot dates
    "history":       { foodId: [pct, ...] },
    "seeded_until":  "2026-02-28"           # crisis-start date; all Hormuz data is real CI
  }
"""
import json
from pathlib import Path

REPO_ROOT   = Path(__file__).parent.parent
HISTORY_DIR = REPO_ROOT / "data" / "history"
SUMMARY_FILE = REPO_ROOT / "data" / "history-summary.json"
CRISIS_START = "2026-02-28"


def main() -> None:
    index_file = HISTORY_DIR / "index.json"
    if not index_file.exists():
        print("Error: data/history/index.json not found")
        return

    with open(index_file) as f:
        dates: list[str] = json.load(f)

    # Load food IDs from first snapshot
    food_ids: list[str] = []
    for d in dates:
        snap_file = HISTORY_DIR / f"{d}.json"
        if snap_file.exists():
            snap = json.load(open(snap_file))
            food_ids = [item["id"] for item in snap.get("foods", [])]
            break

    if not food_ids:
        print("Error: no snapshots found")
        return

    # Build history arrays — prepend crisis-start baseline (0% exposure for all)
    all_dates = [CRISIS_START] + dates
    history_map: dict[str, list[int]] = {fid: [0] for fid in food_ids}

    for d in dates:
        snap_file = HISTORY_DIR / f"{d}.json"
        if not snap_file.exists():
            continue
        snap = json.load(open(snap_file))
        snap_foods = {item["id"]: item["crisis_exposure_pct"] for item in snap.get("foods", [])}
        for fid in food_ids:
            history_map[fid].append(snap_foods.get(fid, 0))

    summary = {
        "dates":        all_dates,
        "history":      history_map,
        "seeded_until": CRISIS_START,  # Hormuz data is all real CI from crisis start
    }

    with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
        json.dump(summary, f)
        f.write("\n")

    print(f"Wrote {SUMMARY_FILE}: {len(all_dates)} dates, {len(food_ids)} foods")


if __name__ == "__main__":
    main()
