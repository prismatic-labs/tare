# 🌾 tare

**The Strait of Hormuz crisis, in your grocery bill.**

A data tool that maps how the 2026 Hormuz supply chain disruption flows through energy, fertilizer, and logistics into the price of everyday food.

→ **[See the tool](https://prismatic-labs.github.io/tare/)**

---

## What is this?

Many foods rely on inputs that move through the Strait of Hormuz: diesel for tractors, natural gas for greenhouses, nitrogen fertilizer for grain, petrochemicals for packaging. Since 28 February 2026, disruptions to the strait have affected an estimated 20% of global oil supply ([EIA](https://www.eia.gov/todayinenergy/detail.php?id=39932)), 20% of LNG ([IEA](https://www.iea.org/reports/the-role-of-gas-in-todays-energy-transitions)), and around 30% of globally traded fertilizer ([IFA](https://www.ifastat.org/)).

**Tare** quantifies how much of each food's retail price is exposed to those supply chain disruptions — across 59 everyday foods and 10 countries.

---

## How it works

Each food is scored by its **crisis exposure** — the percentage of its retail cost that depends on Hormuz-linked inputs (crude oil, diesel, natural gas, urea, ammonia, petrochemicals). The score is computed from:

- USDA and FAO cost-of-production breakdowns for each food category
- Real-time commodity prices from the World Bank, Frankfurter (ECB), and Eurostat
- Country-level Hormuz import dependency data

**Severity bands:**
| Band | Range | Meaning |
|------|-------|---------|
| Extreme | 60–100% | Most of the retail price is crisis-driven |
| High | 40–59% | Significant portion of cost is crisis-exposed |
| Moderate | 20–39% | Meaningful but partial exposure |
| Low | 0–19% | Largely insulated from the crisis |

Data updates weekly via GitHub Actions. No API keys required to view the site.

---

## Why "tare"?

In measurement, **tare** is the weight of the container — what you subtract to find the true weight of the contents. This tool separates the supply-chain component of food prices from the rest.

In botany, tare is a plant in the genus *Vicia* — the same family as [vetch](https://github.com/prismatic-labs/vetch), our other project. Vetch fixes nitrogen in the soil. Tare grows among the wheat, revealing what's hidden. Same family, different job.

---

## Data sources

All free, all open:

| Source | What it provides |
|--------|-----------------|
| [World Bank Commodity Prices](https://www.worldbank.org/en/research/commodity-markets) | Monthly oil, gas, urea, methanol prices |
| [FAO Food Price Index](https://www.fao.org/worldfoodsituation/foodpricesindex/en/) | Meat, dairy, cereals, oils, sugar indices |
| [US EIA Petroleum Data](https://www.eia.gov/opendata/) | WTI, Brent, diesel, gasoline prices |
| [Eurostat](https://ec.europa.eu/eurostat) | HICP food & energy prices by country |
| [Frankfurter API](https://www.frankfurter.app/) | EUR-based exchange rates (ECB source, no key) |
| [FRED Economic Data](https://fred.stlouisfed.org/) | CPI food components, energy prices |

---

## Run locally

Just open `index.html` in a browser — no server required.

To update the data:

```bash
pip install requests pandas openpyxl
python3 scripts/fetch-data.py
```

The script is idempotent and fails gracefully: if any data source is unavailable, it keeps the previous values and continues.

---

## Deployment

The site is a single static HTML file served via GitHub Pages. Deployment is automatic on every push to `main` via `.github/workflows/pages.yml`. Data updates run every Monday at 06:00 UTC via `.github/workflows/update-data.yml`.

---

## Part of Prismatic Labs

Tare is built by [Prismatic Labs](https://prismaticlabs.ai), the team behind [vetch](https://github.com/prismatic-labs/vetch). We build the sensing layer for planet-aware AI — measuring what systems depend on, from GPU tokens to grocery prices.

---

## License

Apache 2.0
