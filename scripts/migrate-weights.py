#!/usr/bin/env python3
"""
migrate-weights.py — One-time migration to add driver weights, stickiness
coefficients, and local cost floors to data/foods.json.

Run once:  python3 scripts/migrate-weights.py

Sources for weights:
  - USDA ERS Commodity Costs and Returns
    https://www.ers.usda.gov/data-products/commodity-costs-and-returns/
  - USDA Food Dollar Series
    https://www.ers.usda.gov/data-products/food-dollar-series/
  - Gemini/academic synthesis of ICIO coefficients
"""
import json
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DATA_FILE = REPO_ROOT / "data" / "foods.json"

# ── Category-level default driver weights ──────────────────────────────────
# Weights represent the fraction of Hormuz-sensitive cost each driver
# contributes. They are applied to each driver of the matching category.
# Weights are normalised to 1.0 across each food's actual drivers.
#
# Grounded in:
#   Grains/Bread:    USDA ERS shows fertilizer ≈35-40% of operating cost,
#                    energy (gas) ≈20%, transport ≈10%
#   Meat (poultry):  Feed grain (fertilizer-intensive) dominates; gas for
#                    heating; diesel for logistics
#   Seafood:         Fishing vessel fuel is the single largest variable cost
#                    (IEA Fish & Ships 2021). Shipping = refrigerated logistics.
#   Produce:         Cold chain diesel + refrigerated shipping dominate
#   Packaged/Proc:   Factory gas (heat/steam) + global container shipping
#   Dairy:           Feed grain (fertilizer) + barn heating (gas)
#   Staples:         Field crop nitrogen + transport; no refrigeration

CATEGORY_WEIGHTS: dict[str, dict[str, float]] = {
    "grains":   {"gas": 0.40, "fertilizer": 0.30, "fuel": 0.20, "shipping": 0.10, "petrochemical": 0.05},
    "meat":     {"fertilizer": 0.40, "gas": 0.20, "fuel": 0.20, "shipping": 0.15, "petrochemical": 0.05},
    "seafood":  {"fuel": 0.70, "shipping": 0.25, "gas": 0.03, "fertilizer": 0.01, "petrochemical": 0.01},
    "produce":  {"fuel": 0.40, "shipping": 0.35, "gas": 0.15, "fertilizer": 0.08, "petrochemical": 0.02},
    "packaged": {"gas": 0.45, "shipping": 0.25, "petrochemical": 0.20, "fuel": 0.07, "fertilizer": 0.03},
    "dairy":    {"fertilizer": 0.35, "gas": 0.30, "fuel": 0.25, "petrochemical": 0.08, "shipping": 0.02},
    "staples":  {"fertilizer": 0.50, "fuel": 0.30, "gas": 0.12, "shipping": 0.06, "petrochemical": 0.02},
}

# ── Per-category stickiness (λ) ─────────────────────────────────────────────
# λ = fraction of cost shock NOT passed to consumer in 30 days.
# Harvard: retailers have menu costs; staples are highly sticky.
# λ=0.8 → only 20% of shock reaches shelf in 30 days.
# λ=0.2 → 80% passes through immediately (perishable luxury goods).
CATEGORY_STICKINESS: dict[str, float] = {
    "grains":   0.80,  # Long shelf life; supermarkets lock in bread prices weeks ahead
    "meat":     0.50,  # Medium; fresh cut prices move weekly
    "seafood":  0.25,  # Low; perishable, spot-priced at fish markets
    "produce":  0.30,  # Low; fresh, high-velocity, repriced daily
    "packaged": 0.70,  # High; manufacturer contracts, promotional calendars
    "dairy":    0.60,  # Medium-high; milk prices regulated in many markets
    "staples":  0.75,  # High; commodity shelf goods, long contracts
}

# ── Per-category local cost floor ──────────────────────────────────────────
# Fraction of retail price that is always local (labour, land, local energy,
# retail margin). Crisis exposure cannot eat into this floor.
# Source: USDA Food Dollar Series — labour+retail margin ≈50-65% for most foods.
CATEGORY_FLOOR: dict[str, float] = {
    "grains":   0.55,  # Retail margin + labour + local energy large for processed
    "meat":     0.40,  # Slaughterhouse + retail labour significant
    "seafood":  0.35,  # Labour-intensive; dockside handling
    "produce":  0.45,  # Farm labour + local retail
    "packaged": 0.50,  # Manufacturing overhead + retail
    "dairy":    0.45,  # Processing + cold retail chain
    "staples":  0.60,  # Mostly local handling; low-value commodity
}

# ── Food-level overrides ────────────────────────────────────────────────────
# Where USDA data gives us sharper estimates for specific foods.
FOOD_OVERRIDES: dict[str, dict] = {
    # Salmon: fuel for long-haul trawlers is the #1 cost — override seafood default
    "salmon":        {"stickiness": 0.20, "floor": 0.30},
    # Shrimp: labour-intensive aquaculture in SE Asia, less sticky than wild catch
    "shrimp":        {"stickiness": 0.20, "floor": 0.35},
    # Eggs: USDA farm share ~40% (highest of any food); very fast pass-through
    "eggs":          {"stickiness": 0.25, "floor": 0.35},
    # Bread: staple with the highest menu-cost stickiness
    "bread":         {"stickiness": 0.85, "floor": 0.55},
    # Frozen pizza: packaged but fresh-ingredient chain, faster than shelf packaged
    "frozen_pizza":  {"stickiness": 0.55, "floor": 0.45},
    # Coffee: global futures market, immediate commodity price transmission
    "coffee":        {"stickiness": 0.30, "floor": 0.40},
    # Avocado: airfreighted, highly perishable — very low stickiness
    "avocado":       {"stickiness": 0.15, "floor": 0.40},
    # Butter: dairy fat prices extremely volatile, ECB data shows fast pass-through
    "butter":        {"stickiness": 0.35, "floor": 0.40},
    # Sugar: government price controls common; high stickiness
    "sugar":         {"stickiness": 0.80, "floor": 0.55},
    # Instant noodles: palm oil + wheat — both sensitive; moderate stickiness
    "instant_noodles": {"stickiness": 0.60, "floor": 0.45},
}


def assign_driver_weights(drivers: list[dict], category: str) -> list[dict]:
    """
    Assign a normalised weight to each driver based on its commodity category
    and the food's category-level default weights.
    Weights sum to 1.0 across all drivers.
    """
    defaults = CATEGORY_WEIGHTS.get(category, {})
    # Raw weights from lookup
    raw = [defaults.get(d["category"], 0.10) for d in drivers]
    total = sum(raw) or 1.0
    normalised = [round(w / total, 4) for w in raw]
    # Fix rounding so they sum exactly to 1.0
    diff = round(1.0 - sum(normalised), 4)
    if normalised:
        normalised[-1] = round(normalised[-1] + diff, 4)
    return [dict(d, weight=w) for d, w in zip(drivers, normalised)]


def main() -> None:
    with open(DATA_FILE, encoding="utf-8") as fh:
        data = json.load(fh)

    updated_foods = []
    for food in data["foods"]:
        cat = food["category"]
        overrides = FOOD_OVERRIDES.get(food["id"], {})

        # Add weights to drivers
        food["drivers"] = assign_driver_weights(food["drivers"], cat)

        # Add stickiness (λ)
        food["pass_through_30d"] = round(
            1.0 - overrides.get("stickiness", CATEGORY_STICKINESS.get(cat, 0.5)), 2
        )

        # Add local cost floor
        food["local_cost_floor_pct"] = round(
            overrides.get("floor", CATEGORY_FLOOR.get(cat, 0.50)) * 100
        )

        updated_foods.append(food)

    data["foods"] = updated_foods

    tmp = DATA_FILE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    tmp.replace(DATA_FILE)
    print(f"Migrated {len(updated_foods)} foods with driver weights, stickiness, and floor.")


if __name__ == "__main__":
    main()
