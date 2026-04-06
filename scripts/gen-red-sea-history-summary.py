#!/usr/bin/env python3
"""
Regenerate data/red-sea-history-summary.json from data/red-sea-history/*.json snapshots.
Run after fetch-red-sea.py adds a new daily snapshot.

Output schema:
  {
    "dates":           ["2023-11-19", ...],  # crisis-start baseline + all snapshot dates
    "history":         { foodId: [pct, ...] },
    "wci_pct_change":  [0.0, 37.0, ...],     # (WCI - pre_crisis) / pre_crisis * 100 per date
    "seeded_until":    "2026-04-01"           # data up to this date is modelled, not live CI
  }

seeded_until is set to the last date that was seeded by the historical generation script.
Once a real CI fetch produces a snapshot with a date > seeded_until, that date is shown
as "Live data" in the Crisis Pulse chart.
"""
import json
from pathlib import Path

REPO_ROOT    = Path(__file__).parent.parent
HISTORY_DIR  = REPO_ROOT / "data" / "red-sea-history"
SUMMARY_FILE = REPO_ROOT / "data" / "red-sea-history-summary.json"
CRISIS_START = "2023-11-19"
PRE_CRISIS_WCI = 1380.0

# Last date that was seeded by the historical generation script (Gemini).
# Update this value if more historical data is seeded manually in future.
SEEDED_UNTIL = "2026-04-01"


def main() -> None:
    index_file = HISTORY_DIR / "index.json"
    if not index_file.exists():
        print("Error: data/red-sea-history/index.json not found")
        return

    with open(index_file) as f:
        dates: list[str] = json.load(f)

    with open(REPO_ROOT / "data" / "red-sea.json") as f:
        food_ids: list[str] = [food["id"] for food in json.load(f)["foods"]]

    all_dates = [CRISIS_START] + dates
    history_map: dict[str, list[int]] = {fid: [0] for fid in food_ids}
    wci_series: list[float] = [0.0]  # pre-crisis baseline = 0% change

    for d in dates:
        snap_file = HISTORY_DIR / f"{d}.json"
        if not snap_file.exists():
            continue
        snap = json.load(open(snap_file))
        snap_foods = {item["id"]: item["crisis_exposure_pct"] for item in snap.get("foods", [])}
        for fid in food_ids:
            history_map[fid].append(snap_foods.get(fid, 0))

        # WCI % change from pre-crisis baseline
        wci = snap.get("sources", {}).get("drewry_wci_usd_40ft")
        if wci is not None:
            wci_pct = round((wci - PRE_CRISIS_WCI) / PRE_CRISIS_WCI * 100, 1)
        else:
            wci_pct = wci_series[-1]  # carry forward last known value
        wci_series.append(wci_pct)

    summary = {
        "dates":          all_dates,
        "history":        history_map,
        "wci_pct_change": wci_series,
        "seeded_until":   SEEDED_UNTIL,
    }

    with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
        json.dump(summary, f)
        f.write("\n")

    print(f"Wrote {SUMMARY_FILE}: {len(all_dates)} dates, {len(food_ids)} foods, "
          f"WCI range {min(wci_series):.0f}%–{max(wci_series):.0f}%")


if __name__ == "__main__":
    main()
