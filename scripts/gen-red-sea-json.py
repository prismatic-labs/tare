#!/usr/bin/env python3
"""
One-off generator: produces data/red-sea.json from scratch.
Run once: python3 scripts/gen-red-sea-json.py
"""
import json, math, random
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

# ── Red Sea driver price changes (from Drewry WCI data) ────────────────────
# WCI: $1380 → $4200 per 40ft container (+204%)
# Insurance: 0.05 bps → 0.75 bps war-risk premium (+1400% but small absolute cost)
# Rerouting: +10-14 days via Cape of Good Hope, ~40% extra fuel per voyage
DRIVER_PCT = {
    "freight":   204,   # Drewry WCI % change
    "insurance":  50,   # blended effective cost increase (war-risk premium)
    "rerouting":  40,   # Cape detour fuel/time cost (partially captured in WCI)
}

# ── Country multipliers (Suez route dependency) ─────────────────────────────
COUNTRIES = [
    {"code": "IT", "name": "Italy",          "currency": "EUR", "suez_dependency_pct": 95, "impact_multiplier": 1.00, "price_level_index": 1.00, "data_confidence": "high"},
    {"code": "GB", "name": "United Kingdom", "currency": "GBP", "suez_dependency_pct": 90, "impact_multiplier": 0.95, "price_level_index": 1.05, "data_confidence": "high"},
    {"code": "JP", "name": "Japan",          "currency": "JPY", "suez_dependency_pct": 30, "impact_multiplier": 0.30, "price_level_index": 1.00, "data_confidence": "medium"},
    {"code": "PH", "name": "Philippines",    "currency": "PHP", "suez_dependency_pct": 25, "impact_multiplier": 0.25, "price_level_index": 0.45, "data_confidence": "low"},
    {"code": "US", "name": "United States",  "currency": "USD", "suez_dependency_pct": 35, "impact_multiplier": 0.35, "price_level_index": 1.30, "data_confidence": "high"},
    {"code": "DE", "name": "Germany",        "currency": "EUR", "suez_dependency_pct": 95, "impact_multiplier": 1.00, "price_level_index": 0.95, "data_confidence": "high"},
    {"code": "IN", "name": "India",          "currency": "INR", "suez_dependency_pct": 65, "impact_multiplier": 0.65, "price_level_index": 0.45, "data_confidence": "low"},
    {"code": "BR", "name": "Brazil",         "currency": "BRL", "suez_dependency_pct": 45, "impact_multiplier": 0.45, "price_level_index": 0.75, "data_confidence": "medium"},
    {"code": "AU", "name": "Australia",      "currency": "AUD", "suez_dependency_pct": 25, "impact_multiplier": 0.25, "price_level_index": 1.25, "data_confidence": "medium"},
    {"code": "LK", "name": "Sri Lanka",      "currency": "LKR", "suez_dependency_pct": 80, "impact_multiplier": 0.80, "price_level_index": 0.40, "data_confidence": "low"},
]

# ── Food definitions ─────────────────────────────────────────────────────────
# Each entry: (id, exposure_pct, pass_through_30d, drivers_spec)
# drivers_spec: list of (category, weight, input, description)
# exposure_pct is the EU (IT/DE) base exposure

FOODS_SPEC = [
    # ── Seafood ─────────────────────────────────────────────────────────────
    ("shrimp", 55, 0.75, [
        ("freight",   0.70, "Container shipping",   "SE Asian farmed shrimp travels ~11,000 km to European ports via Suez"),
        ("insurance", 0.20, "War-risk insurance",   "Red Sea transit insurance up 15× since Nov 2023"),
        ("rerouting", 0.10, "Cape detour",          "Extra 10–14 days via Cape of Good Hope adds refrigeration costs"),
    ], "Farmed shrimp from Vietnam and Thailand is one of the most Suez-dependent proteins. Every tonne moving to European supermarkets passes through the Bab-el-Mandeb strait."),

    ("canned_tuna", 50, 0.70, [
        ("freight",   0.70, "Container shipping",   "Thai and Philippine canneries export exclusively in 20/40ft containers"),
        ("insurance", 0.20, "War-risk insurance",   "Vessel insurance for Red Sea routing has surged 15× since late 2023"),
        ("rerouting", 0.10, "Cape detour",          "Cape route adds 14 days and roughly 40% extra bunker fuel per voyage"),
    ], "Thailand processes over a third of the world's canned tuna. Every can in European stores was packed into a container that once sailed past Bab-el-Mandeb — now rerouted around Africa."),

    ("salmon", 25, 0.60, [
        ("freight",   0.70, "Air/sea freight",      "Norwegian salmon to Asia routes disrupted; some supply reallocation hits Europe"),
        ("insurance", 0.30, "War-risk insurance",   "Perishable cargo commands higher war-risk premiums"),
    ], "European farmed salmon mostly travels short intra-European routes unaffected by Suez, but global supply reallocation and higher packaging import costs add modest pressure."),

    ("canned_sardines", 30, 0.65, [
        ("freight",   0.65, "Container shipping",   "Moroccan and Portuguese sardines exported in steel cans, some via Suez lanes"),
        ("insurance", 0.25, "War-risk insurance",   "Mediterranean-origin vessels still pay elevated war-risk premiums"),
        ("rerouting", 0.10, "Route disruption",     "Suez congestion affects Mediterranean export timing"),
    ], "Moroccan sardines dominate European canned seafood imports. The Atlantic-to-Mediterranean leg is shorter, but elevated insurance and port congestion still filter through."),

    ("cod", 22, 0.55, [
        ("freight",   0.65, "Container shipping",   "Frozen cod from Iceland and Norway shipped in reefer containers"),
        ("insurance", 0.35, "War-risk insurance",   "Reefer container premiums have risen with Red Sea risk"),
    ], "North Atlantic cod rarely transits Suez, but reefer container spot rates have risen globally as capacity is absorbed by rerouting around Africa."),

    # ── Meat ────────────────────────────────────────────────────────────────
    ("chicken", 10, 0.35, [
        ("freight",   0.55, "Packaging imports",   "Polystyrene trays and plastic wrap sourced from SE Asia"),
        ("insurance", 0.45, "Indirect cost",       "Haulage and port handling costs rise with global freight inflation"),
    ], "Chicken is mostly produced locally in Europe with domestic feed, insulating it from container shipping costs. Indirect exposure comes from imported packaging and processing equipment."),

    ("beef", 8, 0.30, [
        ("freight",   0.55, "Packaging/ingredients","Some seasoning and packaging sourced via long-haul container routes"),
        ("insurance", 0.45, "Freight inflation",   "Global container market tightness raises even non-Suez transport costs"),
    ], "European beef production is largely self-contained. The Red Sea crisis has minimal direct impact; modest cost pass-through reflects imported packaging and equipment parts."),

    ("pork", 8, 0.30, [
        ("freight",   0.55, "Packaging/ingredients","Packaging and some feed additives travel in containers"),
        ("insurance", 0.45, "Freight inflation",   "Wider freight market inflation touches pork supply chain margins"),
    ], "Pork production in Europe relies on domestic feed crops and local abattoirs. Container-route disruption has minimal direct effect."),

    ("lamb", 8, 0.30, [
        ("freight",   0.55, "Import costs",        "New Zealand lamb to Europe was partly routed via Suez"),
        ("insurance", 0.45, "War-risk premium",    "NZ lamb ships pay elevated insurance for Red Sea passage"),
    ], "New Zealand lamb exports to Europe historically routed via Suez for speed; Cape rerouting adds two weeks and ~$400/container in fuel costs, partially passed on at retail."),

    ("turkey", 8, 0.30, [
        ("freight",   0.55, "Packaging imports",   "Plastic and film packaging sourced from Asian suppliers"),
        ("insurance", 0.45, "Freight inflation",   "Elevated global container rates add indirect cost pressure"),
    ], "Turkey is produced domestically across Europe. Container-route disruption affects imported packaging and specialty seasoning more than the protein itself."),

    ("sausages", 20, 0.45, [
        ("freight",   0.65, "Ingredient imports",  "Spices, casings and specialty ingredients from Asia and East Africa"),
        ("insurance", 0.35, "War-risk premium",    "Imported ingredient containers face higher insurance"),
    ], "Sausages contain a diverse supply chain: casings from China, spices from East Africa and Asia, all moving through or near the Bab-el-Mandeb strait."),

    # ── Dairy ────────────────────────────────────────────────────────────────
    ("milk", 5, 0.25, [
        ("freight",   0.50, "Packaging",           "Tetra Pak materials and plastics partially sourced from Asia"),
        ("insurance", 0.50, "Freight inflation",   "Wider container market inflation touches packaging supply chains"),
    ], "Fresh milk is nearly entirely local. The Red Sea crisis reaches it only through imported Tetra Pak materials and other packaging components."),

    ("cheese", 8, 0.30, [
        ("freight",   0.55, "Packaging imports",   "Some specialty cheeses packaged with imported materials"),
        ("insurance", 0.45, "Freight inflation",   "Global container inflation adds marginal cost"),
    ], "European cheese production is predominantly local with domestic milk. Minimal exposure to Red Sea disruption beyond packaging costs."),

    ("butter", 8, 0.30, [
        ("freight",   0.55, "NZ butter imports",   "New Zealand butter exports routed Cape due to Red Sea"),
        ("insurance", 0.45, "War-risk insurance",  "NZ vessels pay elevated premiums for former Suez routing"),
    ], "New Zealand is Europe's largest butter import source. Cape rerouting has added weeks to delivery times and elevated spot prices modestly."),

    ("yoghurt", 8, 0.30, [
        ("freight",   0.55, "Packaging",           "Polypropylene cups and lids sourced from Asian manufacturers"),
        ("insurance", 0.45, "Freight inflation",   "Container market tightness adds indirect cost"),
    ], "Yoghurt is produced locally from domestic milk. Container disruption touches it mainly through imported pot and lid manufacturing."),

    ("eggs", 5, 0.25, [
        ("freight",   0.50, "Packaging",           "Cardboard trays use pulp partly sourced from SE Asian mills"),
        ("insurance", 0.50, "Freight inflation",   "Marginal cost increase from global container market"),
    ], "Eggs are one of the most locally-produced foods in Europe. Red Sea disruption has negligible direct impact."),

    ("cream", 8, 0.30, [
        ("freight",   0.55, "Packaging",           "UHT cream packaging materials from Asian suppliers"),
        ("insurance", 0.45, "Freight inflation",   "Global container rates add marginal cost"),
    ], "Cream is produced from domestic milk. Container shipping costs affect mainly packaging inputs."),

    # ── Grains ───────────────────────────────────────────────────────────────
    ("bread", 8, 0.25, [
        ("freight",   0.55, "Packaging imports",   "Polyethylene bags and wrapping sourced from Asian factories"),
        ("insurance", 0.45, "Freight inflation",   "Global container market tightness adds indirect cost"),
    ], "Bread is baked locally using European wheat, largely insulating it from Suez shipping disruption. Imported packaging is the main exposure point."),

    ("rice", 35, 0.65, [
        ("freight",   0.70, "Container shipping",  "Asian rice — Thai jasmine, Indian basmati — ships entirely in containers via Suez"),
        ("insurance", 0.20, "War-risk insurance",  "Rice vessels from India, Pakistan and Thailand face elevated Red Sea insurance"),
        ("rerouting", 0.10, "Cape detour",         "Cape rerouting adds 10–14 days to rice voyages from South Asia"),
    ], "European rice imports are dominated by Asian varieties (basmati, jasmine) that transit the Bab-el-Mandeb. Every bag of imported rice in a supermarket crossed the Red Sea shipping corridor."),

    ("pasta", 20, 0.45, [
        ("freight",   0.65, "Ingredient imports",  "Semolina from North Africa and packaging from Asia travel partially via Suez"),
        ("insurance", 0.35, "War-risk premium",    "Mediterranean supply chains face elevated insurance costs"),
    ], "Italian and European pasta is made from locally grown durum wheat, but some North African semolina and Asian packaging components add modest Suez exposure."),

    ("flour", 8, 0.25, [
        ("freight",   0.55, "Packaging",           "Multi-wall paper bags partially sourced from overseas mills"),
        ("insurance", 0.45, "Freight inflation",   "Global freight inflation adds marginal cost"),
    ], "European flour is milled from locally grown wheat. Red Sea disruption affects it mainly through imported packaging materials."),

    ("oats", 10, 0.30, [
        ("freight",   0.55, "Packaging",           "Cardboard oat packaging partially sourced from Asian mills"),
        ("insurance", 0.45, "Freight inflation",   "Container market tightness adds indirect cost"),
    ], "Oats are grown and processed in Europe. Modest exposure comes through imported packaging and specialty oat varieties from North America."),

    ("cornflakes", 15, 0.40, [
        ("freight",   0.60, "Import ingredients",  "Corn syrup, vitamins and specialty coatings often sourced from Asia"),
        ("insurance", 0.40, "War-risk premium",    "Food additive imports face elevated container insurance"),
    ], "Cornflakes are packaged locally but rely on imported corn syrup concentrates, vitamins and food colorings that route via container through Suez."),

    ("couscous", 28, 0.55, [
        ("freight",   0.65, "Import shipping",     "North African couscous exports route partly via Mediterranean/Suez lanes"),
        ("insurance", 0.25, "War-risk insurance",  "Mediterranean exporters pay elevated insurance premiums"),
        ("rerouting", 0.10, "Route disruption",    "Suez congestion affects North Africa–Europe trade timing"),
    ], "Couscous is imported from North Africa (Morocco, Algeria) and the Middle East. The short Suez-adjacent route means elevated insurance is the primary transmission mechanism."),

    ("noodles", 45, 0.70, [
        ("freight",   0.70, "Container shipping",  "Instant noodles are manufactured in SE Asia and exported in high volumes via Suez"),
        ("insurance", 0.20, "War-risk insurance",  "Asian food exporters face steeply higher Red Sea premiums"),
        ("rerouting", 0.10, "Cape detour",         "Cape rerouting adds 2+ weeks to Asian noodle deliveries"),
    ], "Instant noodles are an extreme case: manufactured in bulk in Thailand, Indonesia and China, packed in containers, and shipped directly through the Bab-el-Mandeb corridor to Europe."),

    ("crackers", 15, 0.40, [
        ("freight",   0.60, "Import ingredients",  "Palm oil, sesame and specialty flavourings from SE Asia"),
        ("insurance", 0.40, "War-risk premium",    "SE Asian ingredient containers face higher insurance"),
    ], "Crackers manufactured in Europe still import palm oil, sesame seeds and some coatings from SE Asian suppliers, all routed via the Suez corridor."),

    # ── Produce ─────────────────────────────────────────────────────────────
    ("tomatoes", 8, 0.30, [
        ("freight",   0.55, "Packaging imports",   "Plastic packaging and transport trays from Asian manufacturers"),
        ("insurance", 0.45, "Freight inflation",   "Global container rates affect packaging cost"),
    ], "European tomatoes are grown domestically or in North Africa. The Red Sea crisis affects mainly packaging components sourced from Asia."),

    ("potatoes", 5, 0.20, [
        ("freight",   0.50, "Packaging",           "Polypropylene bags and plastic film from Asian producers"),
        ("insurance", 0.50, "Freight inflation",   "Marginal container cost increase"),
    ], "Potatoes are one of Europe's most locally-produced foods. Essentially no direct Red Sea exposure."),

    ("onions", 5, 0.20, [
        ("freight",   0.50, "Packaging",           "Net bags and packaging materials from Asian suppliers"),
        ("insurance", 0.50, "Freight inflation",   "Marginal impact from container market"),
    ], "Onions are produced domestically across Europe and North Africa. Red Sea disruption has negligible impact."),

    ("bananas", 15, 0.40, [
        ("freight",   0.60, "Reefer shipping",     "Some banana shipments from East Africa routed via Red Sea"),
        ("insurance", 0.40, "War-risk premium",    "Reefer vessels face elevated insurance for Red Sea-adjacent routing"),
    ], "European bananas come mainly from Latin America (unaffected), but East African banana imports (Kenya, Tanzania) transit the Bab-el-Mandeb, adding modest upward pressure on blended prices."),

    ("apples", 5, 0.20, [
        ("freight",   0.50, "Packaging",           "Protective foam netting and packaging from Asian manufacturers"),
        ("insurance", 0.50, "Freight inflation",   "Marginal impact from container market"),
    ], "European apples are grown in Italy, France, Poland and Spain. Essentially no Suez dependency."),

    ("oranges", 10, 0.30, [
        ("freight",   0.55, "Import shipping",     "South African and Egyptian oranges routed near Bab-el-Mandeb"),
        ("insurance", 0.45, "War-risk premium",    "Southern African reefer ships face elevated insurance"),
    ], "South African citrus exports to Europe previously used the Suez Canal; many vessels now reroute via Cape Town, adding costs absorbed in part by European supermarkets."),

    ("lettuce", 3, 0.15, [
        ("freight",   0.50, "Packaging",           "Clamshell packaging partially from Asian producers"),
        ("insurance", 0.50, "Freight inflation",   "Minimal indirect container cost"),
    ], "Lettuce is perishable and almost entirely local. The Red Sea crisis has essentially no impact."),

    ("peppers", 8, 0.30, [
        ("freight",   0.55, "Import shipping",     "Spanish and Dutch greenhouse peppers use Asian-sourced packaging"),
        ("insurance", 0.45, "Freight inflation",   "Marginal container cost increase"),
    ], "Bell peppers are grown in Spain and the Netherlands. Red Sea disruption affects mainly imported packaging."),

    ("carrots", 5, 0.20, [
        ("freight",   0.50, "Packaging",           "Polypropylene bags from Asian manufacturers"),
        ("insurance", 0.50, "Freight inflation",   "Negligible container market impact"),
    ], "Carrots are produced locally across Europe. Minimal Red Sea exposure."),

    ("avocado", 18, 0.45, [
        ("freight",   0.65, "Reefer shipping",     "East African avocados (Kenya, Tanzania) route via Bab-el-Mandeb"),
        ("insurance", 0.25, "War-risk premium",    "Reefer vessels from East Africa face elevated insurance"),
        ("rerouting", 0.10, "Cape detour",         "Some Kenyan avocados rerouted around Cape, adding 10 days"),
    ], "Europe imports avocados from both Americas (low Suez exposure) and East Africa (high Suez exposure). The blended effect is moderate — Kenyan exports transit directly past the Houthi attack zone."),

    ("garlic", 18, 0.45, [
        ("freight",   0.70, "Container shipping",  "China accounts for ~75% of global garlic exports, shipping via Suez to Europe"),
        ("insurance", 0.20, "War-risk insurance",  "Chinese food exports face elevated Red Sea insurance"),
        ("rerouting", 0.10, "Cape detour",         "Cape rerouting from China to Europe adds 12–14 days"),
    ], "China dominates global garlic production and exports directly to European supermarkets via container ship through the Red Sea. Higher than most produce."),

    ("cucumber", 5, 0.20, [
        ("freight",   0.50, "Packaging",           "Film packaging and trays from Asian suppliers"),
        ("insurance", 0.50, "Freight inflation",   "Marginal container cost increase"),
    ], "Cucumbers are greenhouse-grown in the Netherlands and Spain. Essentially no Suez route dependency."),

    ("frozen_peas", 22, 0.50, [
        ("freight",   0.65, "Reefer containers",   "Frozen vegetables shipped in refrigerated containers partly via Suez"),
        ("insurance", 0.25, "War-risk premium",    "Reefer containers face elevated insurance"),
        ("rerouting", 0.10, "Cape detour",         "Cape rerouting adds costs for Asian and Indian frozen pea exporters"),
    ], "India is a major frozen pea exporter to Europe. Indian frozen vegetable containers transit the Bab-el-Mandeb — now rerouted around Africa at extra cost."),

    # ── Packaged goods ───────────────────────────────────────────────────────
    ("cooking_oil", 45, 0.70, [
        ("freight",   0.70, "Container shipping",  "Palm oil from Malaysia/Indonesia bulk ships and containers route via Suez"),
        ("insurance", 0.20, "War-risk insurance",  "Palm oil carriers face elevated Red Sea premiums"),
        ("rerouting", 0.10, "Cape detour",         "Cape rerouting adds 14+ days from SE Asian palm oil ports"),
    ], "Palm oil — used in virtually all cooking oils — is produced almost exclusively in Malaysia and Indonesia. Every tanker bound for European refineries must navigate near the Red Sea conflict zone."),

    ("frozen_pizza", 45, 0.65, [
        ("freight",   0.70, "Container/reefer",    "Frozen pizza imports and ingredient components in reefer containers"),
        ("insurance", 0.20, "War-risk insurance",  "Refrigerated containers attract elevated premiums"),
        ("rerouting", 0.10, "Cape detour",         "Extra transit time increases refrigeration costs"),
    ], "Frozen pizza combines multiple imported ingredients — palm oil, mozzarella ingredients, tomato paste concentrate — many of which move in containers via the Suez corridor."),

    ("chocolate", 38, 0.65, [
        ("freight",   0.65, "Container shipping",  "Cocoa from Ivory Coast (Atlantic route) and Asian chocolate imports"),
        ("insurance", 0.25, "War-risk premium",    "Specialty chocolate from SE Asia and Middle East faces higher insurance"),
        ("rerouting", 0.10, "Cape detour",         "Some shipping lane diversions affect ingredient timing"),
    ], "West African cocoa (Ivory Coast, Ghana) travels the Atlantic — mostly unaffected. But Asian confectionery imports, cocoa butter from Indonesia, and specialty chocolate all route via Suez, contributing to a moderate blended exposure."),

    ("crisps", 42, 0.68, [
        ("freight",   0.70, "Palm oil & imports",  "SE Asian palm oil for frying and imported Asian crisps ship via Suez"),
        ("insurance", 0.20, "War-risk insurance",  "Bulk vegetable oil carriers face elevated Red Sea premiums"),
        ("rerouting", 0.10, "Cape detour",         "Palm oil from Indonesia/Malaysia adds 2 weeks via Cape"),
    ], "Crisps depend on palm oil for frying — sourced almost entirely from Malaysia and Indonesia. This single ingredient makes crisps highly exposed to Red Sea disruption."),

    ("biscuits", 40, 0.65, [
        ("freight",   0.68, "Palm oil & imports",  "Palm oil, cocoa and Asian-packaged biscuit imports via Suez"),
        ("insurance", 0.22, "War-risk premium",    "Food commodity vessels face elevated insurance"),
        ("rerouting", 0.10, "Cape detour",         "SE Asian ingredient suppliers rerouted around Cape"),
    ], "Biscuits are heavy consumers of palm oil and often include imported ingredients (cocoa powder from SE Asia, specialty flavourings) that transit the Bab-el-Mandeb."),

    ("soft_drinks", 30, 0.55, [
        ("freight",   0.65, "Ingredients import",  "High-fructose corn syrup, citric acid, and colouring from Asia/Americas via Suez"),
        ("insurance", 0.35, "War-risk premium",    "Container-shipped syrups and concentrates face elevated insurance"),
    ], "Soft drink concentrates and ingredient syrups are often produced in the US, Asia and Middle East and shipped to European bottling plants in containers via the Suez route."),

    ("beer", 20, 0.45, [
        ("freight",   0.60, "Hop/malt imports",    "Specialty hops from New Zealand, malt from Australia ship via long routes"),
        ("insurance", 0.40, "Freight inflation",   "Global container market tightness adds indirect cost"),
    ], "Most European beer uses local barley and hops. Craft breweries importing specialty hops from New Zealand or Australia, and imported canned beers from Asia, carry higher exposure."),

    ("coffee", 48, 0.72, [
        ("freight",   0.70, "Container shipping",  "Ethiopian, Yemeni and Vietnamese coffee beans ship directly through Bab-el-Mandeb"),
        ("insurance", 0.20, "War-risk insurance",  "East African coffee exporters face highest Red Sea insurance premiums"),
        ("rerouting", 0.10, "Cape detour",         "Ethiopia and Yemen exports previously used short Red Sea route"),
    ], "Coffee is one of the most Red Sea-exposed foods. Ethiopian and Yemeni coffee — considered premium — ships directly from ports at the Red Sea entrance. Vietnamese robusta (the world's #2 bean) also passes through Bab-el-Mandeb."),

    ("sugar", 32, 0.60, [
        ("freight",   0.65, "Container shipping",  "Cane sugar from India, Pakistan and East Africa routes via Suez"),
        ("insurance", 0.25, "War-risk insurance",  "Sugar carriers from South Asia face elevated Red Sea premiums"),
        ("rerouting", 0.10, "Cape detour",         "Indian and Pakistani sugar exports rerouted around Africa"),
    ], "India and Pakistan are major sugar exporters to Europe. Their shipping lanes pass directly through the Bab-el-Mandeb, making sugar moderately exposed to Red Sea freight disruption."),

    ("peanut_butter", 45, 0.68, [
        ("freight",   0.70, "Container shipping",  "Chinese and SE Asian peanuts and peanut butter ship via Suez in bulk"),
        ("insurance", 0.20, "War-risk premium",    "Commodity food vessels from Asia face elevated insurance"),
        ("rerouting", 0.10, "Cape detour",         "Chinese peanut exports via Cape add 12–14 days"),
    ], "Peanut butter production relies on peanuts from China (the world's largest producer) and Argentina (Atlantic route). The Chinese supply chain is heavily Suez-dependent."),

    ("orange_juice", 18, 0.45, [
        ("freight",   0.60, "Some Suez routing",   "South African and Indian OJ concentrate ships via Bab-el-Mandeb"),
        ("insurance", 0.40, "War-risk premium",    "OJ tankers from South Africa and India pay elevated premiums"),
    ], "Brazilian OJ concentrate takes the Atlantic route — unaffected. But South African and Indian OJ, plus some concentrate from Egypt, travels via the Suez corridor, adding modest blended exposure."),

    ("mayonnaise", 25, 0.55, [
        ("freight",   0.65, "Ingredient imports",  "Soybean oil from South America and egg powder from SE Asia via various routes"),
        ("insurance", 0.35, "War-risk premium",    "Some ingredient containers route via Suez"),
    ], "Mayonnaise relies on vegetable oil (partly imported palm or soy from SE Asia) and egg powder, with moderate Suez route dependency."),

    ("tomato_sauce", 22, 0.50, [
        ("freight",   0.65, "Ingredient imports",  "Tomato concentrate and packaging from SE Asia and North Africa"),
        ("insurance", 0.35, "War-risk premium",    "Some ingredient containers route via Mediterranean/Suez lanes"),
    ], "Tomato sauce ingredients (concentrate, spices, packaging) partly imported from North Africa and Asia via Suez-adjacent routes."),

    ("baby_formula", 48, 0.72, [
        ("freight",   0.70, "Container shipping",  "Australian and NZ whey, and Asian formula (Danone/Nestlé Asia) all route via Suez"),
        ("insurance", 0.20, "War-risk premium",    "High-value food cargo commands elevated war-risk insurance"),
        ("rerouting", 0.10, "Cape detour",         "NZ and Australian dairy container ships now reroute via Cape"),
    ], "Baby formula is heavily imported from Australia, New Zealand and Asian manufacturing hubs. NZ dairy containers previously transited Suez for speed; Cape rerouting adds 12 days and significant cost."),

    # ── Staples ──────────────────────────────────────────────────────────────
    ("lentils", 12, 0.40, [
        ("freight",   0.60, "Container shipping",  "Canadian lentils (world #1 exporter) ship via Atlantic to Europe; Indian lentils via Suez"),
        ("insurance", 0.40, "War-risk premium",    "Indian and Turkish lentil exports face elevated Red Sea insurance"),
    ], "Canada and Australia supply most of Europe's lentils via Atlantic and Pacific routes (low Suez exposure). Indian lentil imports carry moderate exposure, giving a blended low result."),

    ("chickpeas", 12, 0.40, [
        ("freight",   0.60, "Container shipping",  "Australian chickpeas via Pacific; Indian and Turkish via Suez"),
        ("insurance", 0.40, "War-risk premium",    "Indian chickpea exports face elevated Bab-el-Mandeb premiums"),
    ], "Chickpeas come from both Australia (Cape or Pacific route) and India/Turkey (Suez route). Blended exposure is low."),

    ("tofu", 28, 0.55, [
        ("freight",   0.65, "Container shipping",  "Soy protein and pre-made tofu from China and SE Asia via Suez"),
        ("insurance", 0.25, "War-risk premium",    "Chinese and SE Asian food exports face elevated insurance"),
        ("rerouting", 0.10, "Cape detour",         "Chinese tofu and soy ingredients rerouted via Cape"),
    ], "Specialty tofu and soy protein concentrate imported from China and Japan must navigate the Red Sea container corridor, giving tofu moderate Suez exposure."),

    ("beans", 8, 0.30, [
        ("freight",   0.55, "Container imports",   "Some bean varieties (black beans, kidney beans) imported from SE Asia and East Africa"),
        ("insurance", 0.45, "Freight inflation",   "Container market tightness adds indirect cost"),
    ], "Most European beans (haricot, flageolet) are grown domestically or in the Americas. Specialty imported varieties carry some Suez exposure."),

    ("quinoa", 10, 0.35, [
        ("freight",   0.55, "South American origin","Peruvian and Bolivian quinoa ships via Atlantic to Europe"),
        ("insurance", 0.45, "Freight inflation",   "Global container market tightness affects even non-Suez routes"),
    ], "Quinoa is produced in Peru and Bolivia and ships via the Atlantic — largely unaffected by the Red Sea crisis. Modest exposure reflects global container market tightness."),

    ("olive_oil", 5, 0.20, [
        ("freight",   0.50, "Local supply chain",  "Mediterranean-origin olive oil uses very short shipping routes"),
        ("insurance", 0.50, "Freight inflation",   "Marginal global container inflation impact"),
    ], "Olive oil is produced in Spain, Italy, Greece and Tunisia and shipped via short Mediterranean routes that largely bypass the Bab-el-Mandeb."),
]

# ── Helper functions ─────────────────────────────────────────────────────────
def severity(pct: int) -> str:
    if pct >= 60: return "extreme"
    if pct >= 40: return "high"
    if pct >= 20: return "moderate"
    return "low"

def make_food(food_id: str, exposure_pct: int, pass_through: float,
              drivers_spec: list, explanation: str,
              base_food: dict) -> dict:
    floor = base_food["local_cost_floor_pct"]
    cap = 100 - floor
    # Clamp exposure to floor cap
    exposure = min(cap, exposure_pct)
    floor_capped = (exposure == cap)

    # Exposure band (±15% MC uncertainty, or collapsed if floor-capped)
    if floor_capped:
        exp_low = exp_high = exposure
    else:
        exp_low  = max(1, round(exposure * 0.87))
        exp_high = min(cap, round(exposure * 1.13))
        # Ensure point estimate is within band
        exp_low  = min(exp_low,  exposure)
        exp_high = max(exp_high, exposure)

    # Build drivers
    drivers = []
    for cat, weight, inp, desc in drivers_spec:
        drivers.append({
            "input":            inp,
            "description":      desc,
            "category":         cat,
            "price_change_pct": DRIVER_PCT[cat],
            "weight":           weight,
        })

    # Estimated current price (rough: base * (1 + exposure/100 * pass_through * 0.5))
    base = base_food["base_price_eur_kg"]
    current = round(base * (1 + exposure / 100 * pass_through * 0.4), 2)
    projected = round(base * (1 + exposure / 100 * pass_through * 0.7), 2)

    return {
        "id":                   food_id,
        "name":                 base_food["name"],
        "category":             base_food["category"],
        "emoji":                base_food["emoji"],
        "crisis_exposure_pct":  exposure,
        "severity":             severity(exposure),
        "base_price_eur_kg":    base,
        "current_price_eur_kg": current,
        "projected_price_eur_kg": projected,
        "drivers":              drivers,
        "explanation":          explanation,
        "pass_through_30d":     pass_through,
        "local_cost_floor_pct": floor,
        "dietary":              base_food["dietary"],
        "exposure_low":         exp_low,
        "exposure_high":        exp_high,
    }

# ── Load base food data ───────────────────────────────────────────────────────
with open(REPO_ROOT / "data" / "foods.json") as f:
    base_data = json.load(f)

base_by_id = {f["id"]: f for f in base_data["foods"]}

# ── Build foods list ──────────────────────────────────────────────────────────
foods = []
for (food_id, exposure_pct, pass_through, drivers_spec, explanation) in FOODS_SPEC:
    if food_id not in base_by_id:
        raise ValueError(f"Unknown food id: {food_id}")
    foods.append(make_food(food_id, exposure_pct, pass_through,
                           drivers_spec, explanation, base_by_id[food_id]))

# Verify all 59 foods are present
spec_ids = {s[0] for s in FOODS_SPEC}
base_ids = {f["id"] for f in base_data["foods"]}
missing = base_ids - spec_ids
if missing:
    print(f"WARNING: Missing foods from Red Sea spec: {missing}")

# ── Build final JSON ──────────────────────────────────────────────────────────
out = {
    "last_updated": "2026-04-04",
    "crisis_start": "2023-11-19",
    "crisis_name": "Red Sea / Bab-el-Mandeb disruption",
    "crisis_mechanism": "freight",
    "sources": {
        "drewry_wci_usd_40ft":            4200,
        "drewry_wci_pre_crisis_usd_40ft": 1380,
        "insurance_premium_bps":          0.75,
        "insurance_premium_pre_crisis_bps": 0.05,
        "exchange_rates": base_data["sources"]["exchange_rates"],
        "tipping_points": {
            "port_congestion_risk": True,
            "port_congestion_note": "Rotterdam and Hamburg facing 15-25% longer dwell times due to rerouted volumes",
        },
    },
    "countries": COUNTRIES,
    "foods": foods,
}

out_path = REPO_ROOT / "data" / "red-sea.json"
with open(out_path, "w") as f:
    json.dump(out, f, indent=2, ensure_ascii=False)
    f.write("\n")

print(f"Written {out_path}")
print(f"Foods: {len(foods)} (expected 59)")
print(f"Missing from spec: {missing or 'none'}")
print()
print("Exposure summary:")
for tier, lo, hi in [("extreme", 60, 100), ("high", 40, 59), ("moderate", 20, 39), ("low", 0, 19)]:
    count = sum(1 for f in foods if lo <= f["crisis_exposure_pct"] <= hi)
    print(f"  {tier:10} ({lo}-{hi}%): {count} foods")
