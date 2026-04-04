#!/usr/bin/env python3
"""
fetch-red-sea.py — Daily data refresh for the Red Sea / Bab-el-Mandeb crisis page.

Updates data/red-sea.json with:
  - Current Drewry World Container Index (WCI) from the Drewry website
  - Fallback: Freightos Baltic Index (FBX) public JSON
  - Current exchange rates from Frankfurter API (ECB, no key required)
  - Recalculated crisis_exposure_pct for each food based on current WCI

Run manually:  python3 scripts/fetch-red-sea.py
In CI:        Called by .github/workflows/update-data.yml

Dependencies: requests, pydantic (optional, falls back to manual validation)
"""

import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import requests

# --- Try to import pydantic (used for API response validation) ---
try:
    from pydantic import BaseModel, ConfigDict, ValidationError, field_validator
    HAS_PYDANTIC = True
except ImportError:
    HAS_PYDANTIC = False

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# ─── Paths ─────────────────────────────────────────────────────────────────
REPO_ROOT  = Path(__file__).parent.parent
DATA_FILE  = REPO_ROOT / "data" / "red-sea.json"
FOODS_FILE = REPO_ROOT / "data" / "foods.json"  # for exchange rates fallback

# ─── Crisis baseline ────────────────────────────────────────────────────────
CRISIS_START = "2023-11-19"

# ─── Pre-crisis WCI baseline ────────────────────────────────────────────────
PRE_CRISIS_WCI        = 1380.0   # $/40ft container, Nov 2023
PRE_CRISIS_INS_BPS    = 0.05     # war-risk premium, % of cargo value
CURRENT_INS_BPS       = 0.75     # war-risk premium, % of cargo value (estimated)

# ─── Drewry WCI scrape ──────────────────────────────────────────────────────
# The Drewry WCI page publishes the composite index figure in the page HTML.
# We scrape the number; if this breaks, fall through to FBX.
DREWRY_URL = "https://www.drewry.co.uk/supply-chain-advisors/supply-chain-expertise/world-container-index-assessed-by-drewry"

# ─── Freightos FBX fallback ─────────────────────────────────────────────────
# FBX publishes a public JSON endpoint with composite and per-route indices.
# The composite "FBX" key is the global market proxy we use.
FBX_URL = "https://fbx.freightos.com/api/v1/indexes"

# ─── Frankfurter exchange rates ─────────────────────────────────────────────
FRANKFURTER_URL    = "https://api.frankfurter.app/latest?base=EUR"
TARGET_CURRENCIES  = ["GBP", "JPY", "PHP", "USD", "INR", "BRL", "AUD", "LKR"]

# ─── WCI plausibility range ─────────────────────────────────────────────────
WCI_MIN, WCI_MAX = 500.0, 25_000.0  # $/40ft container


# ─── Optional Pydantic models ───────────────────────────────────────────────
if HAS_PYDANTIC:
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


# ─── Data loading ────────────────────────────────────────────────────────────
def load_existing() -> dict[str, Any]:
    with open(DATA_FILE, encoding="utf-8") as fh:
        return json.load(fh)


# ─── WCI fetching ────────────────────────────────────────────────────────────
def _parse_wci_from_drewry_html(html: str) -> Optional[float]:
    """
    Attempt to extract the WCI composite figure from Drewry's webpage HTML.
    The page typically contains a pattern like '$X,XXX' or 'USD X,XXX' near the
    WCI headline. This is fragile — return None if no match found.
    """
    # Look for dollar amounts in range $500–$25,000 near 'WCI' or 'composite'
    patterns = [
        # "$4,200" style
        r'\$\s*([\d,]+(?:\.\d+)?)\s*(?:per\s+(?:40ft|FEU)|composite)',
        # "4,200 USD" style
        r'([\d,]+(?:\.\d+)?)\s*USD\s*(?:per\s+(?:40ft|FEU)|composite)',
        # "WCI ... $4,200" (lookahead)
        r'WCI[^$]*\$([\d,]+(?:\.\d+)?)',
        # Generic: any dollar amount in plausible range following "index"
        r'[Ii]ndex[^$]*\$([\d,]+(?:\.\d+)?)',
    ]
    for pat in patterns:
        for m in re.finditer(pat, html):
            raw = m.group(1).replace(',', '')
            try:
                val = float(raw)
                if WCI_MIN <= val <= WCI_MAX:
                    log.info("  Drewry HTML parse → WCI = %.0f", val)
                    return val
            except ValueError:
                continue
    return None


def fetch_drewry_wci(current_wci: float, stale: list[str]) -> float:
    """
    Attempt to fetch the Drewry WCI composite figure.
    Returns current_wci unchanged on failure.
    """
    try:
        resp = requests.get(DREWRY_URL, timeout=25, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        val = _parse_wci_from_drewry_html(resp.text)
        if val is not None:
            return val
        log.warning("  Drewry HTML parse found no WCI value")
    except requests.RequestException as exc:
        log.warning("  Drewry fetch failed: %s", exc)

    stale.append("drewry_wci")
    return current_wci


def fetch_fbx(current_wci: float, stale: list[str]) -> float:
    """
    Fallback: Freightos Baltic Index public API.
    Returns current_wci unchanged on failure.
    """
    try:
        resp = requests.get(FBX_URL, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        # FBX API returns a list of indexes; look for composite "FBX" entry
        if isinstance(data, list):
            for entry in data:
                if entry.get("code") in ("FBX", "FBX00") and "value" in entry:
                    val = float(entry["value"])
                    if WCI_MIN <= val <= WCI_MAX:
                        log.info("  FBX fallback → %.0f", val)
                        return val
        elif isinstance(data, dict):
            for key in ("FBX", "composite", "global"):
                if key in data:
                    val = float(data[key])
                    if WCI_MIN <= val <= WCI_MAX:
                        log.info("  FBX fallback (key=%s) → %.0f", key, val)
                        return val
        log.warning("  FBX response parse found no usable composite value")
    except (requests.RequestException, ValueError, KeyError) as exc:
        log.warning("  FBX fetch failed: %s", exc)

    stale.append("freightos_fbx")
    return current_wci


def fetch_wci(current: dict[str, Any], stale: list[str]) -> float:
    """
    Try Drewry first, then FBX, then return existing value.
    """
    current_wci = current["sources"].get("drewry_wci_usd_40ft", 4200.0)
    log.info("Fetching WCI from Drewry…")
    wci = fetch_drewry_wci(current_wci, stale)
    if wci == current_wci and "drewry_wci" in stale:
        log.info("Trying Freightos FBX fallback…")
        wci = fetch_fbx(current_wci, stale)
    return wci


# ─── Exchange rates ──────────────────────────────────────────────────────────
def _validate_frankfurter(data: Any) -> dict[str, float]:
    if not isinstance(data, dict):
        raise ValueError(f"expected dict, got {type(data).__name__}")
    if "rates" not in data:
        raise ValueError("missing 'rates' key")
    if HAS_PYDANTIC:
        try:
            return _FrankfurterResponse(**data).rates
        except ValidationError as exc:
            first = exc.errors()[0]
            raise ValueError(f"bad rate: {first['msg']}") from exc
    rates = data["rates"]
    if not isinstance(rates, dict):
        raise ValueError(f"'rates' should be dict, got {type(rates).__name__}")
    out: dict[str, float] = {}
    for code, val in rates.items():
        if not isinstance(val, (int, float)) or val <= 0:
            raise ValueError(f"bad rate for {code!r}: {val!r}")
        out[code] = float(val)
    return out


def fetch_exchange_rates(current_rates: dict[str, float], stale: list[str]) -> dict[str, float]:
    rates = dict(current_rates)
    log.info("Fetching exchange rates from Frankfurter API…")
    try:
        resp = requests.get(
            FRANKFURTER_URL,
            params={"symbols": ",".join(TARGET_CURRENCIES)},
            timeout=15,
        )
        resp.raise_for_status()
        validated = _validate_frankfurter(resp.json())
        for code, val in validated.items():
            if code in TARGET_CURRENCIES:
                rates[code] = val
        log.info("  Exchange rates updated")
    except (requests.RequestException, ValueError) as exc:
        log.warning("  Frankfurter API failed: %s — keeping existing rates", exc)
        stale.append("frankfurter_exchange_rates")
    rates["EUR"] = 1.0
    return rates


# ─── Exposure recalculation ──────────────────────────────────────────────────
def compute_driver_changes(wci: float) -> dict[str, float]:
    """
    Compute % change in each driver category vs pre-crisis baseline.

    freight   = WCI % change vs pre-crisis
    insurance = blended insurance cost increase (war-risk premium, expressed as
                a simplified % cost-impact factor for the model)
    rerouting = fuel and time cost of Cape route (approximately 60% of freight)
    """
    wci_chg = (wci - PRE_CRISIS_WCI) / PRE_CRISIS_WCI * 100.0
    ins_chg = (CURRENT_INS_BPS - PRE_CRISIS_INS_BPS) * 100.0  # 0.70 → 70 effective basis pts
    return {
        "freight":   round(wci_chg, 1),
        "insurance": round(ins_chg * 0.8, 1),   # partial pass-through from premium spike
        "rerouting": round(wci_chg * 0.6, 1),   # cape fuel component of freight
    }


def recalc_driver_pct(driver: dict[str, Any], changes: dict[str, float]) -> dict[str, Any]:
    """Update a single driver's price_change_pct from current market changes."""
    cat     = driver.get("category", "freight")
    new_pct = changes.get(cat)
    if new_pct is not None and new_pct > 0:
        driver = dict(driver)
        driver["price_change_pct"] = int(round(new_pct))
    return driver


def recalc_food_exposure(food: dict[str, Any], changes: dict[str, float]) -> dict[str, Any]:
    """
    Recalculate crisis_exposure_pct for a Red Sea food item.

    Uses the same weighted-driver model as fetch-data.py:
      sensitivity  = original_exposure / mean(original price_change_pcts)
      new_exposure = weighted_mean(updated_pcts) * sensitivity
      clamped      = max(1, min(100 - local_cost_floor_pct, new_exposure))

    No Monte Carlo for Red Sea (simpler model, fewer driver categories).
    """
    import random
    MONTE_CARLO_RUNS = 500
    WEIGHT_NOISE     = 0.15

    food    = dict(food)
    drivers = food.get("drivers", [])
    if not drivers:
        return food

    updated_drivers    = [recalc_driver_pct(d, changes) for d in drivers]
    original_exposure  = food.get("crisis_exposure_pct", 20)
    original_drivers   = food.get("drivers", updated_drivers)
    unweighted_mean    = (
        sum(d["price_change_pct"] for d in original_drivers) / len(original_drivers)
        if original_drivers else 1.0
    )
    sensitivity = original_exposure / max(unweighted_mean, 1.0)
    floor       = float(food.get("local_cost_floor_pct", 45))
    max_exp     = 100.0 - floor

    def _weighted_exp(drvs: list[dict[str, Any]], noise: float = 0.0) -> float:
        ws, wt = 0.0, 0.0
        for d in drvs:
            w   = d.get("weight", 1.0 / len(drvs))
            chg = changes.get(d.get("category", "freight"), 0.0)
            if noise:
                w = max(0.001, w * (1.0 + random.uniform(-noise, noise)))
            ws += w * chg
            wt += w
        if wt <= 0:
            return 0.0
        return max(1.0, min(max_exp, (ws / wt) * sensitivity))

    new_exposure = round(_weighted_exp(updated_drivers))

    # Monte Carlo band
    mc = sorted([_weighted_exp(updated_drivers, WEIGHT_NOISE) for _ in range(MONTE_CARLO_RUNS)])
    p10 = mc[int(MONTE_CARLO_RUNS * 0.10)]
    p90 = mc[int(MONTE_CARLO_RUNS * 0.90)]
    exp_low  = max(1, round(p10))
    exp_high = round(p90)
    # Ensure point estimate is within band
    exp_low  = min(exp_low,  new_exposure)
    exp_high = max(exp_high, new_exposure)

    if new_exposure >= 60:
        severity = "extreme"
    elif new_exposure >= 40:
        severity = "high"
    elif new_exposure >= 20:
        severity = "moderate"
    else:
        severity = "low"

    food["drivers"]              = updated_drivers
    food["crisis_exposure_pct"]  = new_exposure
    food["exposure_low"]         = exp_low
    food["exposure_high"]        = exp_high
    food["severity"]             = severity
    return food


# ─── Atomic write ────────────────────────────────────────────────────────────
def write_atomic(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    tmp.replace(path)
    log.info("Wrote %s", path)


# ─── Main ────────────────────────────────────────────────────────────────────
def main() -> int:
    log.info("=== tare red-sea data refresh — %s ===",
             datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    today   = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    current = load_existing()
    stale: list[str] = []

    # 1. Fetch WCI (Drewry → FBX fallback → cached)
    wci = fetch_wci(current, stale)
    log.info("WCI = %.0f (pre-crisis = %.0f, change = +%.0f%%)",
             wci, PRE_CRISIS_WCI, (wci - PRE_CRISIS_WCI) / PRE_CRISIS_WCI * 100)

    # 2. Fetch exchange rates (re-use Frankfurter — same source as Hormuz page)
    current_rates = current["sources"].get("exchange_rates", {})
    rates = fetch_exchange_rates(current_rates, stale)

    # 3. Compute driver changes from WCI
    changes = compute_driver_changes(wci)
    log.info("Driver changes: %s", changes)

    # 4. Recalculate food exposures
    updated_foods = [recalc_food_exposure(f, changes) for f in current["foods"]]

    # 5. Tipping points
    port_congestion = wci > PRE_CRISIS_WCI * 2
    tipping: dict[str, Any] = {
        "port_congestion_risk":   port_congestion,
        "port_congestion_note":   (
            "Rotterdam and Hamburg facing 15–25% longer dwell times due to rerouted volumes"
            if port_congestion else
            "No active port congestion flag"
        ),
    }

    # 6. Assemble updated data
    sources_update = {
        "drewry_wci_usd_40ft":             wci,
        "drewry_wci_pre_crisis_usd_40ft":  PRE_CRISIS_WCI,
        "insurance_premium_bps":           CURRENT_INS_BPS,
        "insurance_premium_pre_crisis_bps": PRE_CRISIS_INS_BPS,
        "exchange_rates":                  rates,
        "tipping_points":                  tipping,
    }

    updated: dict[str, Any] = {
        "last_updated":     today,
        "crisis_start":     CRISIS_START,
        "crisis_name":      current.get("crisis_name", "Red Sea / Bab-el-Mandeb disruption"),
        "crisis_mechanism": "freight",
        "sources":          sources_update,
        "countries":        current["countries"],
        "foods":            updated_foods,
    }
    if stale:
        updated["stale_sources"] = sorted(set(stale))
        log.warning("Stale sources (cached): %s", updated["stale_sources"])
    else:
        updated["stale_sources"] = []

    # 7. Write atomically
    write_atomic(DATA_FILE, updated)

    log.info("Done. %d foods updated. WCI = %.0f", len(updated_foods), wci)
    return 0


if __name__ == "__main__":
    sys.exit(main())
