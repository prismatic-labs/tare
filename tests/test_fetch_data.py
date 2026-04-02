"""
tests/test_fetch_data.py — Sensitivity & unit tests for fetch-data.py

Run with:  python3 -m pytest tests/ -v
"""

import json
import sys
from pathlib import Path

import pytest

# fetch-data.py uses a hyphen, so standard import won't work — load via importlib
import importlib.util as _ilu

_spec = _ilu.spec_from_file_location(
    "fetch_data",
    Path(__file__).parent.parent / "scripts" / "fetch-data.py",
)
assert _spec and _spec.loader, "Could not locate scripts/fetch-data.py"
_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

PRE_CRISIS                    = _mod.PRE_CRISIS
_validate_frankfurter_response = _mod._validate_frankfurter_response
_validate_wb_row              = _mod._validate_wb_row
compute_commodity_changes     = _mod.compute_commodity_changes
recalc_driver_pct             = _mod.recalc_driver_pct
recalc_food_exposure          = _mod.recalc_food_exposure

# ─── Fixtures ───────────────────────────────────────────────────────────────

BREAD_FOOD = {
    "id": "bread",
    "name": "Bread",
    "crisis_exposure_pct": 28,
    "severity": "moderate",
    "drivers": [
        {"input": "Natural gas", "category": "gas",        "price_change_pct": 38, "description": "Gas for bakeries"},
        {"input": "Diesel",      "category": "fuel",       "price_change_pct": 32, "description": "Transport"},
        {"input": "Urea",        "category": "fertilizer", "price_change_pct": 25, "description": "Nitrogen fertiliser for wheat"},
    ],
}

CHICKEN_FOOD = {
    "id": "chicken",
    "name": "Chicken",
    "crisis_exposure_pct": 73,
    "severity": "extreme",
    "drivers": [
        {"input": "Diesel",   "category": "fuel",       "price_change_pct": 32, "description": "Refrigerated transport"},
        {"input": "Urea",     "category": "fertilizer", "price_change_pct": 25, "description": "Feed grain fertiliser"},
        {"input": "Gas",      "category": "gas",        "price_change_pct": 38, "description": "Poultry house heating"},
        {"input": "Plastics", "category": "petrochemical", "price_change_pct": 20, "description": "Packaging"},
    ],
}


# ─── compute_commodity_changes ───────────────────────────────────────────────

class TestComputeCommodityChanges:
    def test_no_change_from_baseline(self):
        """When prices equal the pre-crisis baseline, all changes are 0."""
        changes = compute_commodity_changes({k: v for k, v in PRE_CRISIS.items()})
        assert changes["oil"] == 0.0
        assert changes["gas"] == 0.0
        assert changes["fertilizer"] == 0.0

    def test_50pct_brent_jump(self):
        """A 50% Brent spike should yield ~50% oil change and ~47.5% fuel change."""
        prices = {**PRE_CRISIS, "oil_brent_usd": PRE_CRISIS["oil_brent_usd"] * 1.5}
        changes = compute_commodity_changes(prices)
        assert changes["oil"] == pytest.approx(50.0, abs=0.5)
        # Diesel tracks oil at 0.95x
        assert changes["fuel"] == pytest.approx(47.5, abs=0.5)

    def test_100pct_gas_jump(self):
        """A 100% gas price spike should yield 100% gas change."""
        prices = {**PRE_CRISIS, "natural_gas_eur_mwh": PRE_CRISIS["natural_gas_eur_mwh"] * 2.0}
        changes = compute_commodity_changes(prices)
        assert changes["gas"] == pytest.approx(100.0, abs=0.5)

    def test_urea_maps_to_fertilizer(self):
        """Urea price change maps to the 'fertilizer' category."""
        prices = {**PRE_CRISIS, "urea_usd_ton": PRE_CRISIS["urea_usd_ton"] * 1.3}
        changes = compute_commodity_changes(prices)
        assert changes["fertilizer"] == pytest.approx(30.0, abs=0.5)

    def test_shipping_tracks_oil_1_1(self):
        """Shipping should track oil at 1.1x."""
        prices = {**PRE_CRISIS, "oil_brent_usd": PRE_CRISIS["oil_brent_usd"] * 1.4}
        changes = compute_commodity_changes(prices)
        assert changes["shipping"] == pytest.approx(changes["oil"] * 1.1, abs=0.5)


# ─── recalc_food_exposure ────────────────────────────────────────────────────

class TestRecalcFoodExposure:
    def test_zero_change_preserves_exposure(self):
        """If commodity prices are unchanged (0% change), driver pcts stay >= 0
        and exposure stays within 1-99."""
        changes = {cat: 0.0 for cat in ("oil", "gas", "fertilizer", "fuel", "petrochemical", "shipping")}
        result = recalc_food_exposure(BREAD_FOOD, changes)
        assert 1 <= result["crisis_exposure_pct"] <= 99

    def test_large_brent_spike_raises_chicken_exposure(self):
        """A 50% oil spike should push high-exposure foods like chicken further up."""
        prices = {**PRE_CRISIS, "oil_brent_usd": PRE_CRISIS["oil_brent_usd"] * 1.5}
        changes = compute_commodity_changes(prices)
        result = recalc_food_exposure(CHICKEN_FOOD, changes)
        # With a 50% oil spike, chicken's exposure should be in 'high' or 'extreme' band
        assert result["crisis_exposure_pct"] >= 40, (
            f"Expected high/extreme exposure, got {result['crisis_exposure_pct']}"
        )

    def test_exposure_clamped_to_99(self):
        """crisis_exposure_pct must never exceed 99."""
        extreme_changes = {cat: 200.0 for cat in ("oil", "gas", "fertilizer", "fuel", "petrochemical", "shipping")}
        result = recalc_food_exposure(CHICKEN_FOOD, extreme_changes)
        assert result["crisis_exposure_pct"] <= 99

    def test_exposure_minimum_is_1(self):
        """crisis_exposure_pct must never drop below 1."""
        tiny_changes = {cat: 0.1 for cat in ("oil", "gas", "fertilizer", "fuel", "petrochemical", "shipping")}
        result = recalc_food_exposure(BREAD_FOOD, tiny_changes)
        assert result["crisis_exposure_pct"] >= 1

    def test_severity_band_matches_pct(self):
        """Severity label must match the computed exposure percentage."""
        prices = {**PRE_CRISIS, "oil_brent_usd": PRE_CRISIS["oil_brent_usd"] * 1.3}
        changes = compute_commodity_changes(prices)
        result = recalc_food_exposure(BREAD_FOOD, changes)
        pct = result["crisis_exposure_pct"]
        expected_severity = (
            "extreme" if pct >= 60 else
            "high"    if pct >= 40 else
            "moderate" if pct >= 20 else
            "low"
        )
        assert result["severity"] == expected_severity

    def test_food_without_drivers_unchanged(self):
        """A food with no drivers should be returned unchanged."""
        food = {"id": "water", "crisis_exposure_pct": 5, "severity": "low", "drivers": []}
        changes = compute_commodity_changes(PRE_CRISIS)
        result = recalc_food_exposure(food, changes)
        assert result["crisis_exposure_pct"] == 5

    def test_bread_lower_than_chicken_under_oil_spike(self):
        """Bread (fewer fuel-intensive drivers) should remain below chicken under an oil spike."""
        prices = {**PRE_CRISIS, "oil_brent_usd": PRE_CRISIS["oil_brent_usd"] * 1.5}
        changes = compute_commodity_changes(prices)
        bread_result   = recalc_food_exposure(BREAD_FOOD, changes)
        chicken_result = recalc_food_exposure(CHICKEN_FOOD, changes)
        assert bread_result["crisis_exposure_pct"] < chicken_result["crisis_exposure_pct"]


# ─── recalc_driver_pct ───────────────────────────────────────────────────────

class TestRecalcDriverPct:
    def test_known_category_updated(self):
        driver = {"input": "Diesel", "category": "fuel", "price_change_pct": 10}
        result = recalc_driver_pct(driver, {"fuel": 45.0})
        assert result["price_change_pct"] == 45

    def test_unknown_category_unchanged(self):
        driver = {"input": "Magic", "category": "unknown_cat", "price_change_pct": 10}
        result = recalc_driver_pct(driver, {"fuel": 45.0})
        assert result["price_change_pct"] == 10

    def test_zero_change_leaves_driver_unchanged(self):
        driver = {"input": "Gas", "category": "gas", "price_change_pct": 38}
        result = recalc_driver_pct(driver, {"gas": 0.0})
        # Zero or negative change → driver kept as-is (not updated by the function)
        assert result["price_change_pct"] == 38

    def test_original_driver_not_mutated(self):
        driver = {"input": "Urea", "category": "fertilizer", "price_change_pct": 25}
        recalc_driver_pct(driver, {"fertilizer": 50.0})
        assert driver["price_change_pct"] == 25  # original untouched


# ─── API response validation (#12) ──────────────────────────────────────────

class TestValidateWbRow:
    def test_valid_oil_price(self):
        assert _validate_wb_row("oil_brent_usd", 85.0) == 85.0

    def test_negative_price_raises(self):
        with pytest.raises(ValueError, match="must be > 0"):
            _validate_wb_row("oil_brent_usd", -5.0)

    def test_non_numeric_raises(self):
        with pytest.raises(ValueError, match="expected numeric"):
            _validate_wb_row("oil_brent_usd", "N/A")

    def test_implausible_oil_price_raises(self):
        """Oil at $5000 is outside the sanity range and should raise."""
        with pytest.raises(ValueError, match="outside expected range"):
            _validate_wb_row("oil_brent_usd", 5000.0)

    def test_unknown_key_passes_without_range_check(self):
        """An unknown commodity key has no sanity range — any positive float is OK."""
        assert _validate_wb_row("some_new_commodity", 123.0) == 123.0


class TestValidateFrankfurterResponse:
    def test_valid_response(self):
        data = {"base": "EUR", "rates": {"USD": 1.08, "GBP": 0.84}}
        result = _validate_frankfurter_response(data)
        assert result["USD"] == pytest.approx(1.08)

    def test_missing_rates_key_raises(self):
        with pytest.raises(ValueError, match="missing 'rates' key"):
            _validate_frankfurter_response({"base": "EUR"})

    def test_non_dict_raises(self):
        with pytest.raises(ValueError, match="expected JSON object"):
            _validate_frankfurter_response([1, 2, 3])

    def test_bad_rate_value_raises(self):
        with pytest.raises(ValueError, match="bad rate"):
            _validate_frankfurter_response({"rates": {"USD": "not_a_number"}})

    def test_negative_rate_raises(self):
        with pytest.raises(ValueError, match="bad rate"):
            _validate_frankfurter_response({"rates": {"USD": -1.0}})


# ─── Integration: foods.json schema spot-check ───────────────────────────────

class TestFoodsJsonSchema:
    """
    Verify data/foods.json has the required structure.
    These tests catch regressions in the data pipeline output.
    """

    DATA_FILE = Path(__file__).parent.parent / "data" / "foods.json"

    @pytest.fixture(scope="class")
    def data(self):
        with open(self.DATA_FILE, encoding="utf-8") as fh:
            return json.load(fh)

    def test_top_level_keys(self, data):
        for key in ("last_updated", "crisis_start", "sources", "countries", "foods"):
            assert key in data, f"Missing top-level key: {key!r}"

    def test_foods_not_empty(self, data):
        assert len(data["foods"]) >= 10, "Expected at least 10 foods"

    def test_each_food_has_required_fields(self, data):
        required = ("id", "name", "category", "emoji", "crisis_exposure_pct", "severity", "drivers")
        for food in data["foods"]:
            for field in required:
                assert field in food, f"Food {food.get('id')!r} missing field {field!r}"

    def test_exposure_in_range(self, data):
        for food in data["foods"]:
            pct = food["crisis_exposure_pct"]
            assert 1 <= pct <= 99, f"{food['id']}: crisis_exposure_pct={pct} out of [1,99]"

    def test_severity_matches_pct(self, data):
        for food in data["foods"]:
            pct = food["crisis_exposure_pct"]
            expected = (
                "extreme" if pct >= 60 else
                "high"    if pct >= 40 else
                "moderate" if pct >= 20 else
                "low"
            )
            assert food["severity"] == expected, (
                f"{food['id']}: pct={pct} → expected {expected!r}, got {food['severity']!r}"
            )

    def test_countries_have_required_fields(self, data):
        required = ("code", "name", "currency", "impact_multiplier", "data_confidence")
        for country in data["countries"]:
            for field in required:
                assert field in country, f"Country {country.get('code')!r} missing {field!r}"

    def test_exchange_rates_present(self, data):
        rates = data["sources"].get("exchange_rates", {})
        assert "EUR" in rates, "exchange_rates must include EUR"
        assert rates["EUR"] == 1.0, "EUR rate must be 1.0"

    def test_stale_sources_is_list(self, data):
        stale = data.get("stale_sources")
        if stale is not None:
            assert isinstance(stale, list), "stale_sources must be a list when present"
