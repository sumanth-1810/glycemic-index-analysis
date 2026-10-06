"""Match glycemic-table foods to USDA SR Legacy foods for carbohydrate type.

The two tables share no identifier, so the match is by name. It is strict on
purpose: a wrong match attaches the wrong nutrient profile to a GI value.

For each GI food, candidates are limited to USDA categories that fit its food
group. A candidate must share the USDA head noun (the text before the first
comma, e.g. "Apples" in "Apples, raw, with skin") and must not contradict the
food's processing label. Survivors are scored on word overlap; the best one is
kept as "accepted" or "review" depending on the score, or dropped.

Starch: USDA measured starch for only ~1,100 SR Legacy foods. Where it is
missing, starch is estimated as total carbohydrate minus total sugars minus
dietary fiber. On foods with both, the estimate correlates 0.96 with measured
starch and runs about 2 g/100 g high, because it also counts minor
carbohydrates such as oligosaccharides.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GI_TABLE = ROOT / "data" / "processed" / "gi_table1.csv"
USDA_ZIP = ROOT / "data" / "raw" / "usda_sr_legacy_2018-04.zip"
USDA_DIR = "FoodData_Central_sr_legacy_food_csv_2018-04/"
OUT = ROOT / "data" / "processed" / "usda_matches.csv"
REVIEW_OUT = ROOT / "data" / "processed" / "usda_review.csv"
REVIEW_FIELDS = ["decision", "match_score", "food_number", "food_name", "processing", "fdc_id", "usda_description"]

ACCEPT_SCORE = 0.95
REVIEW_SCORE = 0.70

NUTRIENTS = {
    "1005": "carb_g",
    "1009": "starch_measured_g",
    "2000": "sugars_g",
    "1079": "fiber_g",
    "1010": "sucrose_g",
    "1011": "glucose_g",
    "1012": "fructose_g",
    "1013": "lactose_g",
    "1014": "maltose_g",
    "1075": "galactose_g",
}

GROUP_CATEGORIES = {
    "BAKERY PRODUCTS": {"Baked Products"},
    "BEVERAGES": {"Beverages", "Fruits and Fruit Juices", "Dairy and Egg Products"},
    "BREADS": {"Baked Products"},
    "BREAKFAST CEREALS": {"Breakfast Cereals", "Cereal Grains and Pasta"},
    "CEREAL GRAINS": {"Cereal Grains and Pasta", "Vegetables and Vegetable Products"},
    "COOKIES": {"Baked Products"},
    "CRACKERS": {"Baked Products", "Snacks"},
    "DAIRY PRODUCTS AND ALTERNATIVES": {"Dairy and Egg Products", "Sweets", "Beverages", "Legumes and Legume Products"},
    "FRUIT AND FRUIT PRODUCTS": {"Fruits and Fruit Juices", "Vegetables and Vegetable Products", "Beverages", "Sweets"},
    "LEGUMES": {"Legumes and Legume Products", "Vegetables and Vegetable Products"},
    "NUTS": {"Nut and Seed Products", "Legumes and Legume Products"},
    "PASTA AND NOODLES": {"Cereal Grains and Pasta"},
    "SNACK FOODS AND CONFECTIONERY": {"Snacks", "Sweets", "Nut and Seed Products"},
    "SOUPS": {"Soups, Sauces, and Gravies"},
    "SUGARS AND SYRUPS": {"Sweets"},
    "VEGETABLES": {"Vegetables and Vegetable Products", "Legumes and Legume Products", "Fruits and Fruit Juices"},
    "REGIONAL OR TRADITIONAL FOODS": {
        "Cereal Grains and Pasta",
        "Vegetables and Vegetable Products",
        "Legumes and Legume Products",
        "Fruits and Fruit Juices",
        "Baked Products",
    },
}

STOPWORDS = {
    "a", "an", "and", "as", "at", "by", "for", "from", "in", "into", "made",
    "of", "on", "or", "the", "to", "with", "without", "prepared", "type", "ns",
    "brand", "regular", "form", "variety", "cultivar", "style",
    "min", "mins", "minute", "minutes", "sec", "h", "hr", "g", "ml", "mg",
    "approx", "tested", "served", "eaten", "consumed", "commercial", "commercially",
    "product", "products", "food", "foods", "mean", "only", "also",
}

SYNONYMS = {
    "wholemeal": "whole wheat",
    "wholewheat": "whole wheat",
    "wholegrain": "whole grain",
    "yoghurt": "yogurt",
    "maize": "corn",
    "kumara": "sweet potato",
    "garbanzo": "chickpea",
    "paw": "papaya",
    "pawpaw": "papaya",
    "capsicum": "pepper",
    "aubergine": "eggplant",
    "courgette": "zucchini",
    "sultana": "raisin",
    "sultanas": "raisins",
    "porridge": "oats",
    "cornflakes": "corn flakes",
    "icecream": "ice cream",
    "ice-cream": "ice cream",
    "flavoured": "flavored",
    "colour": "color",
    "fibre": "fiber",
    "germinated": "sprouted",
    "basmati": "basmati long grain",
    "jasmine": "jasmine long grain",
    "doongara": "doongara long grain",
}

# Variety, flavor, or grain words. If USDA names one and the GI food does not,
# the two are different foods ("Rice, white, glutinous" is not plain rice).
MARKED = {
    "glutinous", "toaster", "wild", "chocolate", "vanilla", "strawberry",
    "blueberry", "banana", "apple", "orange", "cheese", "honey", "raisin",
    "cinnamon", "oat", "bran", "corn", "rye", "oatmeal", "peanut", "sweet",
    "sour", "multigrain", "whole", "pumpernickel", "cracked", "fruit",
    "frosted", "sugar", "sweetened", "salted", "unsalted", "egg", "spinach",
    "vegetable", "buckwheat", "barley", "soy", "coconut", "lemon", "lime",
    "english", "greek", "carbohydrate", "plain", "unsweetened",
}

# Product forms. If the GI food's leading words name one, the USDA food must
# be the same form: mung bean noodles are not mung beans.
FORMS = {
    "noodle", "pasta", "spaghetti", "bread", "flour", "juice", "cake", "muffin",
    "cooky", "cracker", "chip", "bar", "milk", "yogurt", "sauce", "soup",
    "cereal", "porridge", "crisp", "puff", "roll", "bun", "biscuit", "jam",
    "syrup", "pancake", "waffle", "tortilla", "pie", "pudding", "custard",
}

# USDA wording that says nothing about which food it is.
USDA_FILLER = {
    "without", "salt", "added", "drained", "solid", "liquid", "include", "usda",
    "survey", "fndds", "database", "dietary", "study", "commercially",
    "enriched", "unenriched", "common", "mature", "seed", "flesh", "skin",
    "pack", "edible", "portion", "whole", "kernel", "ready", "eat", "heated",
    "frozen", "unprepared", "prepared", "water", "nfs", "type", "such",
}
STATE_WORDS = {
    "raw", "fresh", "boiled", "cooked", "baked", "fried", "roasted", "steamed",
    "microwaved", "canned", "dried", "dehydrated", "dry", "mashed", "instant",
    "quick", "toasted", "uncooked",
}

# A test food whose recipe was altered ("80% wheat flour + 20% chickpea
# flour") is not the generic USDA food, however well the names match.
MODIFIED = re.compile(
    r"\d+(?:\.\d+)?\s*%[^)]*\+"
    r"|\b(?:containing|with|added)\s+\d+(?:\.\d+)?\s*%"
    r"|\bsubstitut|\breplac|\benriched with\b|\bfortified with\b"
    r"|\bhigh-amylose\b|\bresistant starch\b|\blow gi\b|\bhigh[-\s]?fib"
    r"|\+\s*\d|fib(?:re|er)[-\s]?enriched|\b(?:decreased|reduced) gi\b|\bbased on\b"
    # Filled or coated products carry sugar the plain USDA food lacks.
    r"|\bfilled\b|\bcoated\b"
    # Blends and dessert mixes are not the single food their name starts with.
    r"|\s&\s|\bjelly crystals\b",
    flags=re.IGNORECASE,
)

# Drinks are a different form from the whole food wherever the word appears
# in the name: "Apple, pineapple and passionfruit juice" is not an apple.
DRINK_FORMS = {"juice", "drink", "smoothie", "nectar", "shake", "beverage", "dessert"}

# If the GI food says one of these, the USDA food must say it too.
GI_REQUIRES = {"gluten", "brown", "free"}
# A GI food described with the first word cannot match a USDA food described
# with the second (brown rice is not white rice).
OPPOSITES = {("brown", "white"), ("white", "brown"), ("light", "dark"), ("dark", "light")}

# USDA wording for preparation, mapped onto the processing labels.
USDA_STATE = (
    ("raw", r"\braw\b|\bfresh\b"),
    ("cooked", r"\bcooked\b"),
    ("boiled", r"\bboiled\b"),
    ("baked", r"\bbaked\b"),
    ("fried", r"\bfried\b|\bfrench fr"),
    ("roasted", r"\broasted\b"),
    ("steamed", r"\bsteamed\b"),
    ("microwaved", r"\bmicrowaved\b"),
    ("canned", r"\bcanned\b"),
    ("dried", r"\bdried\b|\bdehydrated\b|\bdry\b"),
    ("mashed", r"\bmashed\b"),
    ("instant", r"\binstant\b|\bquick\b"),
    ("toasted", r"\btoasted\b"),
)

# Labels that describe the same broad state in USDA terms.
COMPATIBLE = {
    "boiled": {"boiled", "steamed", "microwaved"},
    "steamed": {"boiled", "steamed"},
    "microwaved": {"boiled", "microwaved", "baked"},
    "pressure-cooked": {"boiled"},
    "parboiled": {"boiled"},
    "instant": {"instant", "boiled", "dried"},
    "baked": {"baked", "roasted"},
    "roasted": {"roasted", "baked"},
    "fried": {"fried"},
    "raw": {"raw"},
    "dried": {"dried"},
    "canned": {"canned"},
    "mashed": {"mashed", "boiled"},
    "toasted": {"toasted", "baked"},
}

COOKED_LABELS = {
    "boiled", "steamed", "microwaved", "pressure-cooked", "parboiled", "instant",
    "baked", "roasted", "fried", "mashed", "toasted", "canned",
}


def state_conflicts(processing: str, state: set[str]) -> bool:
    """True when the USDA preparation contradicts the GI processing label.

    Any USDA state outside the compatible set is a conflict. The bare word
    "cooked" fits every cooked label but not raw.
    """
    if not processing or processing not in COMPATIBLE:
        return False
    allowed = set(COMPATIBLE[processing])
    if processing in COOKED_LABELS:
        allowed.add("cooked")
    return bool(state - allowed)


def _singular(word: str) -> str:
    if len(word) <= 3 or word.endswith("ss"):
        return word
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith("oes"):
        return word[:-2]
    if word.endswith("ches") or word.endswith("shes"):
        return word[:-2]
    if word.endswith("s") and not word.endswith("us"):
        return word[:-1]
    return word


def tokens(text: str, drop_parentheses: bool) -> list[str]:
    text = text.lower()
    text = re.sub(r"[®™©°]", " ", text)
    if drop_parentheses:
        while re.search(r"\([^()]*\)", text):
            text = re.sub(r"\([^()]*\)", " ", text)
    for source, target in SYNONYMS.items():
        text = re.sub(rf"\b{re.escape(source)}\b", target, text)
    words = re.findall(r"[a-z]+", text)
    return [_singular(word) for word in words if word not in STOPWORDS and len(word) > 1]


def usda_state(description: str) -> set[str]:
    lowered = description.lower()
    return {label for label, pattern in USDA_STATE if re.search(pattern, lowered)}


def load_usda() -> list[dict]:
    archive = zipfile.ZipFile(USDA_ZIP)

    def read(name: str) -> list[dict[str, str]]:
        with archive.open(USDA_DIR + name) as handle:
            return list(csv.DictReader(io.TextIOWrapper(handle, encoding="utf-8")))

    categories = {row["id"]: row["description"] for row in read("food_category.csv")}
    nutrients: dict[str, dict[str, float]] = defaultdict(dict)
    with archive.open(USDA_DIR + "food_nutrient.csv") as handle:
        for row in csv.DictReader(io.TextIOWrapper(handle, encoding="utf-8")):
            column = NUTRIENTS.get(row["nutrient_id"])
            if column:
                nutrients[row["fdc_id"]][column] = float(row["amount"])

    foods = []
    allowed = set().union(*GROUP_CATEGORIES.values())
    for row in read("food.csv"):
        category = categories.get(row["food_category_id"], "")
        values = nutrients.get(row["fdc_id"], {})
        if category not in allowed:
            continue
        if not {"carb_g", "sugars_g", "fiber_g"} <= values.keys():
            continue
        if values["carb_g"] < 3:
            continue
        description = row["description"]
        head = description.split(",")[0]
        foods.append(
            {
                "fdc_id": row["fdc_id"],
                "description": description,
                "category": category,
                "head": set(tokens(head, drop_parentheses=False)),
                "tokens": set(tokens(description, drop_parentheses=False)),
                "core": set(tokens(description, drop_parentheses=True)) - USDA_FILLER - STATE_WORDS,
                "state": usda_state(description),
                **values,
            }
        )
    return foods


def score(known: set[str], lead: set[str], candidate: dict) -> float:
    """How much of the USDA description the GI food accounts for.

    GI names carry cultivar, Latin, and cooking detail that USDA never lists,
    so unmatched GI words cost little. Unmatched USDA words are what signal a
    different food. A small recall term breaks ties toward the USDA food that
    explains more of the GI food's leading words.
    """
    core = candidate["core"]
    if not core:
        return 0.0
    precision = len(core & known) / len(core)
    recall = len(lead & candidate["tokens"]) / len(lead) if lead else 0.0
    return precision + 0.1 * recall


def first_segment(name: str) -> str:
    """The food name before its first comma, ignoring parentheses."""
    text = name
    while re.search(r"\([^()]*\)", text):
        text = re.sub(r"\([^()]*\)", " ", text)
    return text.split(",")[0]


def best_match(row: dict[str, str], usda: list[dict]) -> tuple[dict | None, float]:
    allowed = GROUP_CATEGORIES.get(row["food_group"])
    if not allowed:
        return None, 0.0
    gi_tokens = set(tokens(row["food_name"], drop_parentheses=True))
    lead = set(tokens(first_segment(row["food_name"]), drop_parentheses=True))
    heading = set(tokens(row.get("food_subgroup", ""), drop_parentheses=True))
    if not lead:
        return None, 0.0
    processing = row.get("processing", "")
    best = None
    best_score = 0.0
    for candidate in usda:
        if candidate["category"] not in allowed:
            continue
        if not candidate["head"] or not candidate["head"] <= (lead | heading):
            continue
        covered = len(lead & candidate["tokens"]) / len(lead)
        if covered < (1.0 if len(lead) <= 2 else 2 / 3):
            continue
        if (candidate["tokens"] & MARKED) - gi_tokens - heading:
            continue
        if (lead & FORMS) - candidate["tokens"]:
            continue
        if (gi_tokens & DRINK_FORMS) - candidate["tokens"] or (candidate["head"] & DRINK_FORMS) - gi_tokens:
            continue
        if (gi_tokens & GI_REQUIRES) - candidate["tokens"]:
            continue
        if any(a in gi_tokens and b in candidate["tokens"] and b not in gi_tokens for a, b in OPPOSITES):
            continue
        state = candidate["state"]
        if state_conflicts(processing, state):
            continue
        value = score(gi_tokens | heading, lead, candidate)
        if processing and state & COMPATIBLE.get(processing, set()):
            value += 0.05
        elif not processing:
            # With no label, prefer the plain food over a canned or dried one.
            if state - {"raw", "cooked", "boiled"}:
                value -= 0.10
            elif state & {"raw"} and row["food_group"] == "FRUIT AND FRUIT PRODUCTS":
                value += 0.03
        if value > best_score:
            best = candidate
            best_score = value
    return best, best_score


def carbohydrate_profile(candidate: dict) -> dict[str, str]:
    carb = candidate["carb_g"]
    sugars = candidate["sugars_g"]
    fiber = candidate["fiber_g"]
    measured = candidate.get("starch_measured_g")
    if measured is not None:
        starch = measured
        source = "measured"
    else:
        starch = max(carb - sugars - fiber, 0.0)
        source = "estimated"

    def share(value: float) -> str:
        return f"{value / carb:.4f}" if carb > 0 else ""

    sugar_values = {
        f"usda_{name}_g": f"{candidate[name + '_g']:.2f}" if name + "_g" in candidate else ""
        for name in SUGAR_NAMES
    }
    return {
        **sugar_values,
        "usda_carb_g": f"{carb:.2f}",
        "usda_sugars_g": f"{sugars:.2f}",
        "usda_fiber_g": f"{fiber:.2f}",
        "usda_starch_g": f"{starch:.2f}",
        "starch_source": source,
        "sugar_share": share(sugars),
        "fiber_share": share(fiber),
        "starch_share": share(starch),
    }


SUGAR_NAMES = ("glucose", "fructose", "sucrose", "lactose", "maltose", "galactose")

FIELDNAMES = [
    "food_number",
    "food_name",
    "food_group",
    "processing",
    "gi",
    "match_status",
    "match_score",
    "fdc_id",
    "usda_description",
    "usda_category",
    "usda_carb_g",
    "usda_sugars_g",
    "usda_fiber_g",
    "usda_starch_g",
    "starch_source",
    "sugar_share",
    "fiber_share",
    "starch_share",
    *(f"usda_{name}_g" for name in SUGAR_NAMES),
]


def match_all() -> list[dict[str, str]]:
    with GI_TABLE.open(newline="", encoding="utf-8") as handle:
        foods = [row for row in csv.DictReader(handle) if row["row_type"] == "food"]
    usda = load_usda()
    results = []
    for row in foods:
        candidate, value = best_match(row, usda)
        result = {key: "" for key in FIELDNAMES}
        result.update(
            {
                "food_number": row["food_number"],
                "food_name": row["food_name"],
                "food_group": row["food_group"],
                "processing": row.get("processing", ""),
                "gi": row["gi"],
            }
        )
        if candidate is None or value < REVIEW_SCORE:
            result["match_status"] = "none"
        else:
            accepted = value >= ACCEPT_SCORE and not MODIFIED.search(row["food_name"])
            result["match_status"] = "accepted" if accepted else "review"
            result["match_score"] = f"{value:.3f}"
            result["fdc_id"] = candidate["fdc_id"]
            result["usda_description"] = candidate["description"]
            result["usda_category"] = candidate["category"]
            result.update(carbohydrate_profile(candidate))
        results.append(result)
    return results


def write(results: list[dict[str, str]]) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(results)


def write_review(results: list[dict[str, str]]) -> None:
    """Write borderline matches for hand checking.

    Fill the decision column with "accept" or "reject". Re-running the
    matcher keeps decisions already entered for the same food and USDA match.
    """
    kept: dict[tuple[str, str], str] = {}
    if REVIEW_OUT.exists():
        with REVIEW_OUT.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                if row.get("decision"):
                    kept[(row["food_number"], row["fdc_id"])] = row["decision"]
    review = sorted(
        (row for row in results if row["match_status"] == "review"),
        key=lambda row: -float(row["match_score"]),
    )
    with REVIEW_OUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_FIELDS)
        writer.writeheader()
        for row in review:
            entry = {key: row.get(key, "") for key in REVIEW_FIELDS}
            entry["decision"] = kept.get((row["food_number"], row["fdc_id"]), "")
            writer.writerow(entry)


def summarize(results: list[dict[str, str]]) -> str:
    status = Counter(row["match_status"] for row in results)
    by_group: dict[str, Counter] = defaultdict(Counter)
    for row in results:
        by_group[row["food_group"]][row["match_status"]] += 1
    lines = [f"foods {len(results)}"] + [f"  {name:9} {count}" for name, count in status.most_common()]
    lines.append("accepted/review/total by group:")
    for group, counts in by_group.items():
        total = sum(counts.values())
        lines.append(f"  {counts['accepted']:4} {counts['review']:4} {total:5}  {group}")
    return "\n".join(lines)


def main() -> None:
    results = match_all()
    write(results)
    write_review(results)
    print(summarize(results))
    print(f"distinct USDA foods among accepted: {len({row['fdc_id'] for row in results if row['match_status'] == 'accepted'})}")
    print(f"wrote {OUT}")
    print(f"wrote {REVIEW_OUT}")


if __name__ == "__main__":
    main()
