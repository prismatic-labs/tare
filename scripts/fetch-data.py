#!/usr/bin/env python3
from __future__ import annotations
"""
fetch-data.py — Weekly data refresh for prismatic-labs/tare

Updates data/foods.json with:
  - Current commodity prices from the World Bank Pink Sheet
  - Current exchange rates from Frankfurter API (ECB, no key required)
  - Recalculated crisis_exposure_pct for each food based on current prices

Also archives a snapshot to data/history/YYYY-MM-DD.json and maintains
data/history/index.json so the frontend can draw sparklines.

Run manually:  python3 scripts/fetch-data.py
In CI:        Called by .github/workflows/update-data.yml

Dependencies: requests, pandas, openpyxl
"""

import json
import logging
import math
import os
import random
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

# --- Try to import pydantic (used for API response validation) ---
try:
    from pydantic import BaseModel, ConfigDict, ValidationError, field_validator
    HAS_PYDANTIC = True
except ImportError:
    HAS_PYDANTIC = False

# --- Try to import pandas/openpyxl (only needed for World Bank Excel) ---
try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# ─── Paths ─────────────────────────────────────────────────────────────────
REPO_ROOT   = Path(__file__).parent.parent
DATA_FILE   = REPO_ROOT / "data" / "foods.json"
HISTORY_DIR = REPO_ROOT / "data" / "history"

# ─── Crisis baseline date ───────────────────────────────────────────────────
CRISIS_START = "2026-02-28"

# ─── Model constants ────────────────────────────────────────────────────────
MONTE_CARLO_RUNS    = 500   # iterations for uncertainty band
WEIGHT_NOISE        = 0.15  # ±15% uniform noise on driver weights
UREA_TIPPING_POINT  = 600.0  # $/ton — above this, flag scarcity inflation risk
BRENT_SURCHARGE_TRIGGER = 110.0  # $/bbl — above this, carriers activate non-linear bunker surcharges

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

# ─── Multi-source commodity data ──────────────────────────────────────────
# Each commodity is fetched from a cascade of free APIs. The first source
# that returns a valid, in-range value wins. This eliminates single-source
# staleness — if the World Bank API is slow, FRED or Trading Economics
# scraping fills in.
#
# Source priority per commodity:
#   Oil:      FRED (DCOILBRENTEU, daily) → World Bank API → WB Excel
#   Gas:      World Bank API (European spot) → WB Excel
#   Urea:    World Bank API (E. European spot) → WB Excel
#   Methanol: World Bank API → WB Excel

# Conversion: WB gas API reports $/mmbtu; 1 mmbtu ≈ 0.293 MWh → $/MWh = $/mmbtu / 0.293
GAS_MMBTU_TO_MWH = 0.293

# ─── FRED API (Federal Reserve Economic Data) ────────────────────────────
# Free with API key. Daily commodity prices — much fresher than World Bank.
# Key set as FRED_API_KEY env var (also used by Clover).
FRED_API_BASE = "https://api.stlouisfed.org/fred/series/observations"
FRED_SERIES = {
    "oil_brent_usd":       "DCOILBRENTEU",   # Brent crude, daily ($/bbl) — globally priced
    # Gas and urea removed: FRED series are US-domestic prices that don't reflect
    # international markets during a Hormuz crisis. World Bank provides proper
    # European gas and E. European urea prices.
}

# ─── World Bank Commodity Price Data API ──────────────────────────────────
WB_API_BASE = "https://api.worldbank.org/v2/country/all/indicator"
WB_INDICATORS = {
    "PNRGBRENT":   "oil_brent_usd",      # Crude oil, Brent ($/bbl)
    "PNGASEUROP":  "natural_gas_eur_mwh", # Natural gas, Europe ($/mmbtu → converted)
    "PUREA":       "urea_usd_ton",        # Urea, E. Europe ($/mt)
    "PMETHANOL":   "methanol_usd_ton",    # Methanol, US Gulf Coast ($/mt)
}

# ─── World Bank Pink Sheet Excel (third-tier fallback) ────────────────────
WB_EXCEL_URL = (
    "https://thedocs.worldbank.org/en/doc/"
    "5d903e848db1d1b83e0ec8f744e55570-0350012021/"
    "related/CMO-Historical-Data-Monthly.xlsx"
)
WB_EXCEL_SERIES = {
    "Crude oil, Brent":        "oil_brent_usd",
    "Natural gas, Europe":     "natural_gas_eur_mwh",
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


# ─── #12 API response validation ───────────────────────────────────────────

# Pydantic models (used when pydantic is available; fallback to manual checks otherwise)
_COMMODITY_RANGES: dict[str, tuple[float, float]] = {
    "oil_brent_usd":       (10.0,  500.0),
    "natural_gas_eur_mwh": ( 5.0, 1000.0),
    "urea_usd_ton":        (50.0, 2000.0),
    "methanol_usd_ton":    (50.0, 3000.0),
}

if HAS_PYDANTIC:
    class _CommodityPrice(BaseModel):
        model_config = ConfigDict(frozen=True)
        key: str
        value: float

        @field_validator("value")
        @classmethod
        def value_in_range(cls, v: float, info: Any) -> float:
            if v <= 0:
                raise ValueError(f"must be > 0, got {v}")
            key = info.data.get("key", "")
            if key in _COMMODITY_RANGES:
                lo, hi = _COMMODITY_RANGES[key]
                if not (lo <= v <= hi):
                    raise ValueError(
                        f"{key!r}: {v} outside expected range [{lo}, {hi}] — "
                        "possible feed format change"
                    )
            return v

    class _FrankfurterResponse(BaseModel):
        model_config = ConfigDict(extra="ignore")
        rates: dict[str, float]

        @field_validator("rates")
        @classmethod
        def rates_positive(cls, v: dict[str, float]) -> dict[str, float]:
            for code, rate in v.items():
                if rate <= 0:
                    raise ValueError(f"bad rate for {code!r}: {rate}")
            return v


def _validate_wb_row(key: str, val: Any) -> float:
    """
    Validate a single commodity value. Delegates to Pydantic when available,
    falls back to manual checks otherwise.
    Raises ValueError with a descriptive message if the value is unexpected.
    """
    if not isinstance(val, (int, float)):
        raise ValueError(f"World Bank {key!r}: expected numeric, got {type(val).__name__} ({val!r})")
    if HAS_PYDANTIC:
        try:
            return _CommodityPrice(key=key, value=float(val)).value
        except ValidationError as exc:
            raise ValueError(f"World Bank {key!r}: {exc.errors()[0]['msg']}") from exc
    # Manual fallback
    fval = float(val)
    if fval <= 0:
        raise ValueError(f"World Bank {key!r}: implausible value {fval} (must be > 0)")
    if key in _COMMODITY_RANGES:
        lo, hi = _COMMODITY_RANGES[key]
        if not (lo <= fval <= hi):
            raise ValueError(
                f"World Bank {key!r}: {fval} outside expected range [{lo}, {hi}] — "
                "possible sheet format change"
            )
    return fval


def _validate_frankfurter_response(data: Any) -> dict[str, float]:
    """
    Validate the Frankfurter API response shape. Delegates to Pydantic when
    available, falls back to manual checks otherwise.
    Returns the rates dict, or raises ValueError if the shape is wrong.
    """
    if HAS_PYDANTIC:
        if not isinstance(data, dict):
            raise ValueError(f"Frankfurter: expected JSON object, got {type(data).__name__}")
        if "rates" not in data:
            raise ValueError("Frankfurter: missing 'rates' key — API format may have changed")
        try:
            return _FrankfurterResponse(**data).rates
        except ValidationError as exc:
            first = exc.errors()[0]
            raise ValueError(f"Frankfurter: bad rate for {first.get('loc', ('?',))[-1]!r}: {first['msg']}") from exc
    # Manual fallback
    if not isinstance(data, dict):
        raise ValueError(f"Frankfurter: expected JSON object, got {type(data).__name__}")
    if "rates" not in data:
        raise ValueError("Frankfurter: missing 'rates' key — API format may have changed")
    rates = data["rates"]
    if not isinstance(rates, dict):
        raise ValueError(f"Frankfurter: 'rates' should be a dict, got {type(rates).__name__}")
    validated: dict[str, float] = {}
    for code, val in rates.items():
        if not isinstance(val, (int, float)) or val <= 0:
            raise ValueError(f"Frankfurter: bad rate for {code!r}: {val!r}")
        validated[code] = float(val)
    return validated


# ─── Fetch functions ────────────────────────────────────────────────────────

# Keys that must be fetched; if missing after all sources, marked stale.
COMMODITY_KEYS = ("oil_brent_usd", "natural_gas_eur_mwh", "urea_usd_ton", "methanol_usd_ton")


def _fetch_fred(key: str) -> float | None:
    """
    Fetch a single commodity value from the FRED API.
    Returns the value or None if unavailable.
    Requires FRED_API_KEY environment variable.
    """
    api_key = os.environ.get("FRED_API_KEY")
    if not api_key:
        return None
    series_id = FRED_SERIES.get(key)
    if not series_id:
        return None

    try:
        resp = requests.get(
            FRED_API_BASE,
            params={
                "series_id": series_id,
                "api_key": api_key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": 10,
            },
            timeout=15,
        )
        resp.raise_for_status()
        observations = resp.json().get("observations", [])
        for obs in observations:
            val_str = obs.get("value", ".")
            if val_str != ".":
                val = float(val_str)
                return _validate_wb_row(key, val)
        return None
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        log.warning("  FRED %s (%s) failed: %s", series_id, key, exc)
        return None


def _fetch_wb_api_single(indicator: str, key: str) -> float | None:
    """
    Fetch a single commodity from the World Bank Indicators API.
    Returns the value or None if unavailable.
    """
    url = f"{WB_API_BASE}/{indicator}"
    try:
        resp = requests.get(url, params={"format": "json", "mrv": 3, "frequency": "M"}, timeout=20)
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, list) or len(payload) < 2:
            return None
        data_points = payload[1]
        if not isinstance(data_points, list) or not data_points:
            return None
        for dp in data_points:
            if dp.get("value") is not None:
                val = float(dp["value"])
                if key == "natural_gas_eur_mwh":
                    val = val / GAS_MMBTU_TO_MWH
                return _validate_wb_row(key, val)
        return None
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        log.warning("  WB API %s (%s) failed: %s", indicator, key, exc)
        return None


def _fetch_wb_excel_all() -> dict[str, float]:
    """
    Fetch commodity prices from the World Bank Pink Sheet Excel file.
    Returns a dict of successfully fetched {key: value}.
    """
    results: dict[str, float] = {}
    if not HAS_PANDAS:
        log.warning("pandas/openpyxl not installed — cannot fetch Excel fallback")
        return results

    log.info("  Trying World Bank Pink Sheet Excel…")
    try:
        resp = requests.get(WB_EXCEL_URL, timeout=60)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.warning("  Pink Sheet download failed: %s", exc)
        return results

    try:
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp.write(resp.content)
            tmp_path = tmp.name

        df = pd.read_excel(tmp_path, sheet_name="Monthly Prices", header=None)
        os.unlink(tmp_path)
        last_col = df.iloc[0].last_valid_index()

        for _idx, row in df.iterrows():
            series_name = str(row.iloc[0]).strip()
            if series_name in WB_EXCEL_SERIES:
                key = WB_EXCEL_SERIES[series_name]
                raw_val = row[last_col]
                if pd.isna(raw_val):
                    continue
                try:
                    results[key] = _validate_wb_row(key, raw_val)
                    log.info("  Excel %s = %.2f", key, results[key])
                except ValueError as ve:
                    log.warning("  Excel validation error — %s", ve)
    except Exception as exc:
        log.warning("  Pink Sheet parse error: %s", exc)

    return results


def fetch_commodity_prices(current: dict[str, Any], stale: list[str]) -> dict[str, float]:
    """
    Multi-source cascade for commodity prices.

    For each commodity, tries sources in priority order until one succeeds:
      1. FRED API (daily data, freshest — requires FRED_API_KEY)
      2. World Bank Indicators API (monthly)
      3. World Bank Pink Sheet Excel (monthly, slowest)
      4. Cached value from current foods.json (last resort — marked stale)

    This eliminates single-source staleness. If any individual API is down,
    the others fill in. Only marks a commodity as stale if ALL sources fail.
    """
    # Start with cached values as ultimate fallback
    prices: dict[str, float] = {
        k: current["sources"][k]
        for k in COMMODITY_KEYS
        if k in current["sources"]
    }

    # Track which keys still need fresh data
    needed: set[str] = set(COMMODITY_KEYS)
    source_used: dict[str, str] = {}

    # ── Source 1: FRED API (daily, freshest) ──────────────────────────────
    fred_key = os.environ.get("FRED_API_KEY")
    if fred_key:
        log.info("Fetching from FRED API…")
        for key in list(needed):
            val = _fetch_fred(key)
            if val is not None:
                prices[key] = val
                needed.discard(key)
                source_used[key] = "fred"
                log.info("  FRED %s = %.2f", key, val)
    else:
        log.info("FRED_API_KEY not set — skipping FRED source")

    # ── Source 2: World Bank Indicators API (monthly) ─────────────────────
    if needed:
        log.info("Fetching from World Bank API… (need: %s)", ", ".join(sorted(needed)))
        # Build reverse map: key → indicator
        key_to_indicator = {v: k for k, v in WB_INDICATORS.items()}
        for key in list(needed):
            indicator = key_to_indicator.get(key)
            if indicator:
                val = _fetch_wb_api_single(indicator, key)
                if val is not None:
                    prices[key] = val
                    needed.discard(key)
                    source_used[key] = "world_bank_api"
                    log.info("  WB API %s = %.2f", key, val)

    # ── Source 3: World Bank Pink Sheet Excel (monthly, slow) ─────────────
    if needed:
        log.info("Still need: %s — trying Excel fallback", ", ".join(sorted(needed)))
        excel_results = _fetch_wb_excel_all()
        for key in list(needed):
            if key in excel_results:
                prices[key] = excel_results[key]
                needed.discard(key)
                source_used[key] = "world_bank_excel"

    # ── Mark remaining as stale (using cached value) ──────────────────────
    for key in needed:
        stale.append(key)
        source_used[key] = "cached"
        log.warning("  %s: ALL sources failed — using cached value %.2f", key, prices.get(key, 0))

    # Summary
    log.info("Commodity sources: %s", {k: source_used.get(k, "cached") for k in COMMODITY_KEYS})

    return prices


def fetch_exchange_rates(current_rates: dict[str, float], stale: list[str]) -> dict[str, float]:
    """
    Fetch EUR-based exchange rates from the Frankfurter API (ECB source, no key).
    Falls back to existing rates on failure, appending 'frankfurter' to *stale*.
    """
    rates = dict(current_rates)

    log.info("Fetching exchange rates from Frankfurter API…")
    try:
        resp = requests.get(
            FRANKFURTER_URL,
            params={"symbols": ",".join(TARGET_CURRENCIES)},
            timeout=15,
        )
        resp.raise_for_status()
        validated = _validate_frankfurter_response(resp.json())
        for code, val in validated.items():
            if code in TARGET_CURRENCIES:
                rates[code] = val
                log.info("  %s = %.4f", code, val)
    except (requests.RequestException, ValueError) as exc:
        log.warning("Frankfurter API failed: %s — keeping existing rates", exc)
        stale.append("frankfurter_exchange_rates")

    rates["EUR"] = 1.0
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


def _weighted_exposure(drivers: list[dict[str, Any]], changes: dict[str, float],
                       sensitivity: float, floor: float,
                       weight_noise: float = 0.0) -> float:
    """
    Compute crisis_exposure_pct for a single set of drivers using weighted sum.

    Each driver contributes: weight * commodity_change_pct * sensitivity
    The local cost floor caps the maximum possible exposure.

    weight_noise > 0 perturbs weights for Monte Carlo runs (±noise uniform).
    """
    if not drivers:
        return 0.0

    weighted_sum = 0.0
    weight_total = 0.0
    for d in drivers:
        cat  = d.get("category", "fuel")
        chg  = changes.get(cat, 0.0)
        w    = d.get("weight", 1.0 / len(drivers))
        if weight_noise:
            # ±noise uniform perturbation, clamp to (0, 2)
            w = max(0.001, w * (1.0 + random.uniform(-weight_noise, weight_noise)))
        weighted_sum  += w * chg
        weight_total  += w

    if weight_total <= 0:
        return 0.0

    normalised_input_chg = weighted_sum / weight_total
    raw_exposure = normalised_input_chg * sensitivity

    # Apply local cost floor: crisis can only affect the non-floor fraction
    max_exposure = 100.0 - floor
    clamped = max(1.0, min(max_exposure, raw_exposure))
    return clamped


def recalc_food_exposure(food: dict[str, Any], changes: dict[str, float]) -> dict[str, Any]:
    """
    Recalculate a food's crisis_exposure_pct using weighted driver inputs.

    Improvements over the naive mean:
      - Driver weights (from USDA cost-of-production data) replace equal weighting
      - Local cost floor prevents crisis exposure from exceeding (100 - floor)%
      - Monte Carlo uncertainty band (500 runs ±15% weight noise) → exposure_low/high
      - Pass-through coefficient (λ) stored but not applied here — UI uses it for
        display only (shelf price ≠ cost-chain exposure)
    """
    food = dict(food)
    drivers = food.get("drivers", [])

    if not drivers:
        return food

    # Update driver price_change_pct from current commodity prices
    updated_drivers = [recalc_driver_pct(d, changes) for d in drivers]

    # Sensitivity: ratio of calibrated EU exposure to mean unweighted input change.
    # Kept from pre-crisis calibration so the scale stays anchored.
    original_exposure = food.get("crisis_exposure_pct", 30)
    original_drivers  = food.get("drivers", updated_drivers)
    unweighted_mean   = (
        sum(d["price_change_pct"] for d in original_drivers) / len(original_drivers)
        if original_drivers else 1.0
    )
    sensitivity = original_exposure / max(unweighted_mean, 1.0)

    floor = food.get("local_cost_floor_pct", 45) / 100.0 * 100.0  # convert % to raw

    # Point estimate using proper weights
    new_exposure = round(_weighted_exposure(updated_drivers, changes, sensitivity, floor))

    # ── Monte Carlo uncertainty band ────────────────────────────────────────
    mc_results = sorted([
        _weighted_exposure(updated_drivers, changes, sensitivity, floor,
                           weight_noise=WEIGHT_NOISE)
        for _ in range(MONTE_CARLO_RUNS)
    ])
    # 10th–90th percentile band (80% confidence interval)
    p10 = mc_results[int(MONTE_CARLO_RUNS * 0.10)]
    p90 = mc_results[int(MONTE_CARLO_RUNS * 0.90)]
    exposure_low  = max(1, round(p10))
    exposure_high = round(p90)  # floor cap already applied inside _weighted_exposure

    # Severity based on point estimate
    if new_exposure >= 60:
        severity = "extreme"
    elif new_exposure >= 40:
        severity = "high"
    elif new_exposure >= 20:
        severity = "moderate"
    else:
        severity = "low"

    food["drivers"]          = updated_drivers
    food["crisis_exposure_pct"] = new_exposure
    food["exposure_low"]     = exposure_low
    food["exposure_high"]    = exposure_high
    food["severity"]         = severity

    return food


def check_tipping_points(prices: dict[str, float]) -> dict[str, Any]:
    """
    Check commodity prices against known non-linear tipping points.
    Returns a dict of active flags to be stored in the sources block.

    Cambridge: if urea > $600/t, signal shifts from cost-push to scarcity inflation.
    IEA/IATA: if Brent > $110/bbl, carriers trigger non-linear bunker adjustment
    factor (BAF) surcharges — historically ±$200/container per $10/bbl beyond trigger.
    """
    flags: dict[str, Any] = {}

    urea = prices.get("urea_usd_ton", 0.0)
    if urea >= UREA_TIPPING_POINT:
        flags["urea_scarcity_risk"] = True
        flags["urea_scarcity_threshold_usd_ton"] = UREA_TIPPING_POINT
        flags["urea_current_usd_ton"] = round(urea, 1)
        log.warning(
            "TIPPING POINT: Urea at $%.0f/t ≥ threshold $%.0f/t — "
            "scarcity inflation risk active", urea, UREA_TIPPING_POINT
        )
    else:
        flags["urea_scarcity_risk"] = False

    oil = prices.get("oil_brent_usd", 0.0)
    if oil >= BRENT_SURCHARGE_TRIGGER:
        flags["brent_surcharge_risk"] = True
        flags["brent_surcharge_threshold_usd"] = BRENT_SURCHARGE_TRIGGER
        flags["brent_current_usd"] = round(oil, 1)
        log.warning(
            "TIPPING POINT: Brent at $%.1f/bbl ≥ threshold $%.0f/bbl — "
            "non-linear shipping bunker surcharges active", oil, BRENT_SURCHARGE_TRIGGER
        )
    else:
        flags["brent_surcharge_risk"] = False

    return flags


def write_atomic(path: Path, data: dict[str, Any]) -> None:
    """Write JSON atomically via a temp file."""
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    tmp.replace(path)
    log.info("Wrote %s", path)


# ─── #13 History archiving ──────────────────────────────────────────────────

def archive_snapshot(data: dict[str, Any], today: str) -> None:
    """
    Write a dated snapshot to data/history/YYYY-MM-DD.json and update
    data/history/index.json with the list of available dates.

    The snapshot is a compact summary (not the full JSON) to keep the
    history directory small:
      { "date": "...", "foods": [{"id": "...", "crisis_exposure_pct": N}, ...] }
    """
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    snapshot: dict[str, Any] = {
        "date": today,
        "sources": {
            k: data["sources"].get(k)
            for k in ("oil_brent_usd", "natural_gas_eur_mwh", "urea_usd_ton", "methanol_usd_ton")
        },
        "foods": [
            {"id": f["id"], "crisis_exposure_pct": f["crisis_exposure_pct"]}
            for f in data["foods"]
        ],
    }

    snap_path = HISTORY_DIR / f"{today}.json"
    write_atomic(snap_path, snapshot)
    log.info("Archived snapshot → %s", snap_path)

    # Update index
    index_path = HISTORY_DIR / "index.json"
    if index_path.exists():
        with open(index_path, encoding="utf-8") as fh:
            index: list[str] = json.load(fh)
    else:
        index = []

    if today not in index:
        index.append(today)
        index.sort()

    write_atomic(index_path, index)  # type: ignore[arg-type]
    log.info("History index now has %d entries", len(index))


def main() -> int:
    log.info("=== tare data refresh — %s ===", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # 1. Load existing data
    current = load_existing()

    # ── #13: Archive today's *current* snapshot before overwriting ──────────
    # Archive what's currently live so we capture the point-in-time value.
    archive_snapshot(current, current.get("last_updated", today))

    # ── #19: Track which sources fell back to cached data ───────────────────
    stale: list[str] = []

    # 2. Fetch commodity prices (multi-source cascade: FRED → WB API → WB Excel → cached)
    prices = fetch_commodity_prices(current, stale)

    # 3. Fetch exchange rates from Frankfurter (ECB)
    current_rates = current["sources"].get("exchange_rates", {})
    rates = fetch_exchange_rates(current_rates, stale)

    # 4. Compute commodity change percentages
    changes = compute_commodity_changes(prices)
    log.info("Commodity changes vs pre-crisis: %s", changes)

    # 5. Recalculate food exposures
    updated_foods = [recalc_food_exposure(f, changes) for f in current["foods"]]

    # 5b. Check tipping points
    tipping = check_tipping_points(prices)

    # 6. Assemble updated sources block
    sources_update = {
        "oil_brent_usd":                  prices.get("oil_brent_usd",               current["sources"]["oil_brent_usd"]),
        "oil_brent_pre_crisis_usd":        PRE_CRISIS["oil_brent_usd"],
        "natural_gas_eur_mwh":             prices.get("natural_gas_eur_mwh",          current["sources"]["natural_gas_eur_mwh"]),
        "natural_gas_pre_crisis_eur_mwh":  PRE_CRISIS["natural_gas_eur_mwh"],
        "urea_usd_ton":                    prices.get("urea_usd_ton",                 current["sources"]["urea_usd_ton"]),
        "urea_pre_crisis_usd_ton":         PRE_CRISIS["urea_usd_ton"],
        "diesel_eur_litre":                current["sources"].get("diesel_eur_litre", 1.95),
        "diesel_pre_crisis_eur_litre":     PRE_CRISIS["diesel_eur_litre"],
        "methanol_usd_ton":                prices.get("methanol_usd_ton",             current["sources"].get("methanol_usd_ton", 540)),
        "methanol_pre_crisis_usd_ton":     PRE_CRISIS["methanol_usd_ton"],
        "exchange_rates":                  rates,
        "tipping_points":                  tipping,
    }

    # 7. Build final payload — include stale_sources so the UI can flag it
    updated: dict[str, Any] = {
        "last_updated":  today,
        "crisis_start":  CRISIS_START,
        "sources":       sources_update,
        "countries":     current["countries"],
        "foods":         updated_foods,
    }
    if stale:
        updated["stale_sources"] = sorted(set(stale))
        log.warning("Stale sources (cached): %s", updated["stale_sources"])
    else:
        # Explicitly clear any previous stale flag
        updated["stale_sources"] = []

    # 8. Write main data file atomically
    write_atomic(DATA_FILE, updated)

    # ── #13: Also archive the freshly-written snapshot ──────────────────────
    archive_snapshot(updated, today)

    log.info("Done. %d foods updated.", len(updated_foods))
    if stale:
        log.warning("WARNING: %d source(s) used cached data: %s", len(stale), stale)
    return 0


if __name__ == "__main__":
    sys.exit(main())
