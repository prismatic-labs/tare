#!/usr/bin/env python3
"""
Reconstruct missing data/history/ snapshots for a date range.

Written to repair the 2026-08-11 .. 2026-08-27 outage, when update-data.yml
could not push to main after the protect-main ruleset landed. Kept in the repo
because the same failure mode can recur.

WHY A REPLAY AND NOT A PER-DATE RECOMPUTE
-----------------------------------------
recalc_food_exposure() derives `sensitivity` from the *previous* run's
crisis_exposure_pct and driver price_change_pct values:

    sensitivity_n   = exposure_{n-1} / mean(driver_pct_{n-1})
    exposure_n      = weighted(changes_n) * sensitivity_n

The series is therefore path-dependent: you cannot compute 2026-08-20 in
isolation and get the value the daily job would have produced. This script
replays forward one day at a time from a known-good starting state, feeding
each day's output into the next, exactly as the daily job would have.

WHAT IS AND IS NOT RECONSTRUCTED
--------------------------------
  oil_brent_usd        FRED DCOILBRENTEU, daily, queried per date. Genuine.
  natural_gas_eur_mwh  World Bank PNGASEUROP, MONTHLY. Constant across August,
  urea_usd_ton         World Bank PUREA,      MONTHLY. so holding these at the
  methanol_usd_ton     World Bank PMETHANOL,  MONTHLY. August level is correct,
                       not an approximation.

Brent is the only genuinely daily input, and it is fetched per date rather than
interpolated. FRED has no observation on weekends and holidays; the API returns
the most recent prior observation, which matches how a market price carries.

Exposure maths is imported from fetch-data.py rather than reimplemented, so the
backfill cannot silently drift from the live calculation.

Usage:
    python3 scripts/backfill-history.py \
        --start 2026-08-11 --end 2026-08-27 \
        --base-file /tmp/foods-2026-08-10.json

    python3 scripts/backfill-history.py ... --dry-run   # print, write nothing

Requires FRED_API_KEY.
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent

log = logging.getLogger("backfill")


def _load_fetch_data():
    """Import fetch-data.py by path (the hyphen makes it non-importable normally)."""
    path = REPO_ROOT / "scripts" / "fetch-data.py"
    spec = importlib.util.spec_from_file_location("_tare_fetch_data", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fetch_brent_on(day: date, api_key: str) -> float | None:
    """
    Brent close on `day`, or the most recent observation before it.

    FRED publishes DCOILBRENTEU on business days only. observation_end + desc
    ordering returns the latest observation at or before `day`, which is the
    value that would have been live when the daily job ran.
    """
    try:
        resp = requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={
                "series_id": "DCOILBRENTEU",
                "api_key": api_key,
                "file_type": "json",
                "observation_end": day.isoformat(),
                "sort_order": "desc",
                "limit": 10,
            },
            timeout=20,
        )
        resp.raise_for_status()
        for obs in resp.json().get("observations", []):
            if obs.get("value", ".") != ".":
                return float(obs["value"])
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        log.warning("  FRED lookup failed for %s: %s", day, exc)
    return None


def daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", required=True, help="first missing date, YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="last missing date, YYYY-MM-DD")
    ap.add_argument("--base-file", required=True,
                    help="foods.json as it stood the day before --start")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the reconstructed series without writing")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    api_key = os.environ.get("FRED_API_KEY")
    if not api_key:
        log.error("FRED_API_KEY is not set. Brent cannot be reconstructed without it.")
        return 1

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()
    if end < start:
        log.error("--end is before --start")
        return 1
    if end >= datetime.now(timezone.utc).date():
        log.error("--end must be in the past; today's snapshot is the daily job's to write")
        return 1

    fd = _load_fetch_data()

    with open(args.base_file, encoding="utf-8") as fh:
        state: dict[str, Any] = json.load(fh)

    log.info("Replaying %s .. %s from base state dated %s",
             start, end, state.get("last_updated", "?"))

    # Monthly World Bank series — constant across the window, carried from the
    # base state rather than re-fetched, since the API only exposes the current
    # month and that is the same month this gap sits in.
    monthly = {
        k: state["sources"][k]
        for k in ("natural_gas_eur_mwh", "urea_usd_ton", "methanol_usd_ton")
        if k in state["sources"]
    }
    log.info("Holding monthly series constant: %s", monthly)

    existing_skipped: list[str] = []
    written: list[str] = []

    for day in daterange(start, end):
        iso = day.isoformat()
        snap_path = fd.HISTORY_DIR / f"{iso}.json"

        brent = fetch_brent_on(day, api_key)
        if brent is None:
            log.error("  %s: no Brent observation available — aborting rather than "
                      "guessing. Nothing further written.", iso)
            return 1

        prices = {"oil_brent_usd": brent, **monthly}
        changes = fd.compute_commodity_changes(prices)

        state = copy.deepcopy(state)
        state["foods"] = [fd.recalc_food_exposure(f, changes) for f in state["foods"]]
        state["sources"] = {**state["sources"], "oil_brent_usd": brent, **monthly}
        state["last_updated"] = iso

        mean_exp = (sum(f["crisis_exposure_pct"] for f in state["foods"])
                    / max(len(state["foods"]), 1))
        log.info("  %s  brent=%6.2f  oil_chg=%6.1f%%  mean_exposure=%5.1f",
                 iso, brent, changes["oil"], mean_exp)

        if args.dry_run:
            continue
        if snap_path.exists():
            existing_skipped.append(iso)
            continue

        fd.archive_snapshot(state, iso)
        written.append(iso)

    if args.dry_run:
        log.info("Dry run — nothing written.")
        return 0

    if existing_skipped:
        log.warning("Left %d existing snapshot(s) untouched: %s",
                    len(existing_skipped), ", ".join(existing_skipped))
    log.info("Wrote %d snapshot(s).", len(written))

    # The replay ends the day before the live series resumes. Report the seam so
    # a discontinuity against the next real snapshot is visible rather than
    # discovered later in a chart.
    resume = end + timedelta(days=1)
    resume_path = fd.HISTORY_DIR / f"{resume.isoformat()}.json"
    if resume_path.exists() and state.get("foods"):
        with open(resume_path, encoding="utf-8") as fh:
            actual = json.load(fh)
        replayed = {f["id"]: f["crisis_exposure_pct"] for f in state["foods"]}
        deltas = [
            (f["id"], replayed.get(f["id"]), f["crisis_exposure_pct"])
            for f in actual.get("foods", [])
            if replayed.get(f["id"]) is not None
            and replayed[f["id"]] != f["crisis_exposure_pct"]
        ]
        if deltas:
            log.warning("Seam at %s — %d food(s) differ between the replayed %s "
                        "and the live snapshot:", resume, len(deltas), end)
            for fid, was, now in deltas[:10]:
                log.warning("    %-20s replayed %3s -> live %3s", fid, was, now)
            log.warning("This is expected: the live series resumed from the %s state, "
                        "not from the replay. Reported, not silently smoothed.",
                        state.get("last_updated"))
        else:
            log.info("Seam at %s is continuous.", resume)

    return 0


if __name__ == "__main__":
    sys.exit(main())
