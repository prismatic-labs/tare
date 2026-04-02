#!/usr/bin/env python3
"""
fetch-data.py — Weekly data refresh for prismatic-labs/tare

Updates data/foods.json with:
  - Current commodity prices from the World Bank Pink Sheet
  - Current exchange rates from Frankfurter API (ECB, no key required)
  - Recalculated crisis_exposure_pct for each food based on current prices

Run manually:  python3 scripts/fetch-data.py
In CI:        Called by .github/workflows/update-data.yml

Dependencies: requests, pandas, openpyxl
"""

import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

# --- Try to import pandas/openpyxl (only needed for World Bank Excel) ---
try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# ─── Paths ─────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).parent.parent
DATA_FILE = REPO_ROOT / "data" / "foods.json"

# ─── Crisis baseline date ───────────────────────────────────────────────────
CRISIS_START = "2026-02-28"

# ─── Pre-crisis baselines (from foods.json sources block) ──────────────────
# These are used as the denominator when computing % change.
# If we can't fetch new prices, these defaults keep existing pct values intact.
PRE_CRISIS = {
    "oil_brent_usd":               72.0,
    "natural_gas_eur_mwh":         34.0,
    "urea_usd_ton":                320.0,
    "diesel_eur_litre":             1.42,
    "methanol_usd_ton":            400.0,
}

# ─── World Bank Pink Sheet ─────────────────────────────────────────────────
WB_URL = (
    "https://thedocs.worldbank.org/en/doc/"
    "5d903e848db1d1b83e0ec8f744e55570-0350012021/"
    "related/CMO-Historical-Data-Monthly.xlsx"
)

# World Bank series names for commodities we care about
WB_SERIES = {
    "Crude oil, Brent": "oil_brent_usd",
    "Natural gas, Europe": "natural_gas_eur_mwh",
    "Urea, E. Europe, bagged": "urea_usd_ton",
    "Methanol, US Gulf Coast": "methanol_usd_ton",
}

# ─── Frankfurter (ECB) exchange rates ──────────────────────────────────────
FRANKFURTER_URL = "https://api.frankfurter.app/latest?base=EUR"

# Currency codes we need
TARGET_CURRENCIES = ["GBP", "JPY", "PHP", "USD", "INR", "BRL", "AUD", "LKR"]


def load_existing() -> dict[str, Any]:
    """Load current foods.json, returning the dict."""
    with open(DATA_FILE, encoding="utf-8") as fh:
        return json.load(fh)


def fetch_wb_prices(current: dict[str, Any]) -> dict[str, float]:
    """
    Pull the latest commodity prices from the World Bank Pink Sheet Excel file.
    Returns a dict of {source_key: value}. Falls back to current JSON values
    on any failure.
    """
    prices: dict[str, float] = {
        k: current["sources"][k]
        for k in ("oil_brent_usd", "natural_gas_eur_mwh", "urea_usd_ton", "methanol_usd_ton")
        if k in current["sources"]
    }

    if not HAS_PANDAS:
        log.warning("pandas/openpyxl not installed — skipping World Bank Excel fetch")
        return prices

    log.info("Fetching World Bank Pink Sheet…")
    try:
        resp = requests.get(WB_URL, timeout=60)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.warning("World Bank fetch failed: %s — keeping existing prices", exc)
        return prices

    try:
        # The Pink Sheet uses the "Monthly Prices" sheet; series are rows, months are columns
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp.write(resp.content)
            tmp_path = tmp.name

        df = pd.read_excel(tmp_path, sheet_name="Monthly Prices", header=None)
        os.unlink(tmp_path)

        # Find the last column with data (most recent month)
        last_col = df.iloc[0].last_valid_index()

        for idx, row in df.iterrows():
            series_name = str(row.iloc[0]).strip()
            if series_name in WB_SERIES:
                key = WB_SERIES[series_name]
                val = row[last_col]
                if pd.notna(val) and isinstance(val, (int, float)) and val > 0:
                    prices[key] = float(val)
                    log.info("  %s = %.2f", key, val)

    except Exception as exc:
        log.warning("Pink Sheet parse error: %s — keeping existing prices", exc)

    return prices


def fetch_exchange_rates(current_rates: dict[str, float]) -> dict[str, float]:
    """
    Fetch EUR-based exchange rates from the Frankfurter API (ECB source, no key).
    Falls back to existing rates on failure.
    """
    rates = dict(current_rates)  # copy

    log.info("Fetching exchange rates from Frankfurter API…")
    try:
        resp = requests.get(
            FRANKFURTER_URL,
            params={"symbols": ",".join(TARGET_CURRENCIES)},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        for code, val in data.get("rates", {}).items():
            if code in TARGET_CURRENCIES:
                rates[code] = float(val)
                log.info("  %s = %.4f", code, val)
    except requests.RequestException as exc:
        log.warning("Frankfurter API failed: %s — keeping existing rates", exc)

    rates["EUR"] = 1.0  # always pin EUR
    return rates


def compute_commodity_changes(prices: dict[str, float]) -> dict[str, float]:
    """
    Compute percentage change vs pre-crisis baseline for each commodity category.
    Returns a dict of {driver_category: pct_change}.
    """
    def pct_change(current_val: float, baseline: float) -> float:
        if baseline <= 0:
            return 0.0
        return round((current_val - baseline) / baseline * 100, 1)

    oil_chg = pct_change(
        prices.get("oil_brent_usd", PRE_CRISIS["oil_brent_usd"]),
        PRE_CRISIS["oil_brent_usd"],
    )
    gas_chg = pct_change(
        prices.get("natural_gas_eur_mwh", PRE_CRISIS["natural_gas_eur_mwh"]),
        PRE_CRISIS["natural_gas_eur_mwh"],
    )
    urea_chg = pct_change(
        prices.get("urea_usd_ton", PRE_CRISIS["urea_usd_ton"]),
        PRE_CRISIS["urea_usd_ton"],
    )
    methanol_chg = pct_change(
        prices.get("methanol_usd_ton", PRE_CRISIS["methanol_usd_ton"]),
        PRE_CRISIS["methanol_usd_ton"],
    )

    # Diesel tracks oil closely (refining margin is relatively stable)
    diesel_chg = round(oil_chg * 0.95, 1)

    return {
        "oil":          oil_chg,
        "gas":          gas_chg,
        "fertilizer":   urea_chg,
        "fuel":         diesel_chg,
        "petrochemical": round(methanol_chg * 0.7, 1),  # petrochemicals lag methanol
        "shipping":     round(oil_chg * 1.1, 1),        # shipping fuel tracks oil
    }


def recalc_driver_pct(driver: dict[str, Any], changes: dict[str, float]) -> dict[str, Any]:
    """Update a single driver's price_change_pct using current commodity changes."""
    cat = driver.get("category", "fuel")
    new_pct = changes.get(cat)

    if new_pct is not None and new_pct > 0:
        driver = dict(driver)
        driver["price_change_pct"] = int(round(new_pct))
    return driver


def recalc_food_exposure(food: dict[str, Any], changes: dict[str, float]) -> dict[str, Any]:
    """
    Recalculate a food's crisis_exposure_pct from updated driver price changes.

    Method: weighted average of driver price changes, normalised to the
    food's number of drivers. Then scaled to the food's pre-crisis sensitivity
    ratio so that items with fewer direct inputs don't appear over-exposed.
    """
    food = dict(food)
    drivers = food.get("drivers", [])

    if not drivers:
        return food

    # Recalculate individual driver pcts
    updated_drivers = [recalc_driver_pct(d, changes) for d in drivers]

    # Weighted mean of driver pct changes
    total_pct = sum(d["price_change_pct"] for d in updated_drivers)
    mean_input_chg = total_pct / len(updated_drivers)

    # Scale to crisis exposure using a sensitivity factor derived from the
    # original ratio between exposure and mean input change (pre-crisis calibration)
    original_drivers = food.get("drivers", [])
    original_mean = (
        sum(d["price_change_pct"] for d in original_drivers) / len(original_drivers)
        if original_drivers else 1
    )
    original_exposure = food.get("crisis_exposure_pct", 30)
    sensitivity = original_exposure / max(original_mean, 1)

    new_exposure = round(mean_input_chg * sensitivity)
    new_exposure = max(1, min(99, new_exposure))  # clamp to 1-99

    # Determine severity
    if new_exposure >= 60:
        severity = "extreme"
    elif new_exposure >= 40:
        severity = "high"
    elif new_exposure >= 20:
        severity = "moderate"
    else:
        severity = "low"

    food["drivers"] = updated_drivers
    food["crisis_exposure_pct"] = new_exposure
    food["severity"] = severity

    return food


def write_atomic(path: Path, data: dict[str, Any]) -> None:
    """Write JSON atomically via a temp file."""
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    tmp.replace(path)
    log.info("Wrote %s", path)


def main() -> int:
    log.info("=== tare data refresh — %s ===", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))

    # 1. Load existing data
    current = load_existing()

    # 2. Fetch commodity prices from World Bank
    prices = fetch_wb_prices(current)

    # 3. Fetch exchange rates from Frankfurter (ECB)
    current_rates = current["sources"].get("exchange_rates", {})
    rates = fetch_exchange_rates(current_rates)

    # 4. Compute commodity change percentages
    changes = compute_commodity_changes(prices)
    log.info("Commodity changes vs pre-crisis: %s", changes)

    # 5. Recalculate food exposures
    updated_foods = [recalc_food_exposure(f, changes) for f in current["foods"]]

    # 6. Assemble updated sources block
    sources_update = {
        "oil_brent_usd":               prices.get("oil_brent_usd",               current["sources"]["oil_brent_usd"]),
        "oil_brent_pre_crisis_usd":     PRE_CRISIS["oil_brent_usd"],
        "natural_gas_eur_mwh":          prices.get("natural_gas_eur_mwh",          current["sources"]["natural_gas_eur_mwh"]),
        "natural_gas_pre_crisis_eur_mwh": PRE_CRISIS["natural_gas_eur_mwh"],
        "urea_usd_ton":                 prices.get("urea_usd_ton",                 current["sources"]["urea_usd_ton"]),
        "urea_pre_crisis_usd_ton":      PRE_CRISIS["urea_usd_ton"],
        "diesel_eur_litre":             current["sources"].get("diesel_eur_litre", 1.95),
        "diesel_pre_crisis_eur_litre":  PRE_CRISIS["diesel_eur_litre"],
        "methanol_usd_ton":             prices.get("methanol_usd_ton",             current["sources"].get("methanol_usd_ton", 540)),
        "methanol_pre_crisis_usd_ton":  PRE_CRISIS["methanol_usd_ton"],
        "exchange_rates":               rates,
    }

    # 7. Build final payload
    updated = {
        "last_updated":  datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "crisis_start":  CRISIS_START,
        "sources":       sources_update,
        "countries":     current["countries"],
        "foods":         updated_foods,
    }

    # 8. Write atomically
    write_atomic(DATA_FILE, updated)

    log.info("Done. %d foods updated.", len(updated_foods))
    return 0


if __name__ == "__main__":
    sys.exit(main())
