"""Label each food with the processing method described in its name.

The 2021 glycemic table has no processing column. The method is written into
the food name ("boiled 20 min", "baked with skin") or, failing that, the
section heading. A food with neither stays blank.

When a name mentions more than one method, the label is the one named last,
which is the preparation used for the test. Two product classes override that:
"instant" or "pre-cooked" stays instant even if the packet was then
microwaved, and "parboiled" stays parboiled even if the rice was then boiled.
"""

from __future__ import annotations

import csv
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TABLE = ROOT / "data" / "processed" / "gi_table1.csv"

# (label, pattern). Longer, more specific patterns are listed first so a
# span search can prefer them when two patterns match the same words.
PATTERNS: tuple[tuple[str, str], ...] = (
    ("pressure-cooked", r"pressure[-\s]?cook"),
    ("parboiled", r"par-?boil"),
    ("instant", r"\binstant\b|\bpre-?cooked\b"),
    ("microwaved", r"microwave"),
    ("fried", r"deep[-\s]?frie|stir[-\s]?frie|shallow[-\s]?frie|pan[-\s]?frie|\bfried\b|\bfrying\b"),
    ("steamed", r"steam"),
    ("roasted", r"roast|charcoal"),
    ("grilled", r"grill"),
    ("baked", r"\bbak(?:e|ed|ing)\b"),
    ("boiled", r"\bboil(?:ed|ing)?\b|\brice cooker\b|\bcooked\b"),
    ("toasted", r"\btoast(?:ed)?\b"),
    ("fermented", r"ferment"),
    ("canned", r"\bcanned\b|\bbaked beans\b"),
    ("dried", r"\bdried\b"),
    ("mashed", r"\bmashed\b"),
    ("popped", r"\b(?:popped|puffed)\b"),
    ("extruded", r"extrud"),
    ("raw", r"\braw\b|\buncooked\b"),
    ("germinated", r"germinat"),
)

# These words name an ingredient, not the way the tested food was prepared.
FOLLOWED_BY_INGREDIENT = {
    "toasted": r"\s+(?:sesame|almond|coconut|nut|nuts|seed|seeds|flax|hazelnut)",
    "dried": r"\s+(?:fruit|fruits|apricot|grape|raisin|cranberr)",
}

OVERRIDES = ("instant", "parboiled")


def _matches(text: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for label, pattern in PATTERNS:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            start, end = match.span()
            prefix = text[max(0, start - 16) : start]
            if re.search(r"(?:with|plus|containing)\s+$", prefix, flags=re.IGNORECASE):
                continue
            if label == "boiled" and re.search(r"pre-?$", prefix, flags=re.IGNORECASE):
                continue
            ingredient = FOLLOWED_BY_INGREDIENT.get(label)
            if ingredient and re.match(ingredient, text[end:], flags=re.IGNORECASE):
                continue
            found.append((end, label))
    return found


def label_text(text: str) -> str:
    """Return the processing label for one description, or a blank string."""
    matches = _matches(text.lower())
    if not matches:
        return ""
    labels = {label for _, label in matches}
    for override in OVERRIDES:
        if override in labels:
            return override
    return max(matches)[1]


def label_food(name: str, subgroup: str = "") -> tuple[str, str]:
    """Label a food from its name, then from its section heading."""
    from_name = label_text(name)
    if from_name:
        return from_name, "name"
    from_subgroup = label_text(subgroup)
    if from_subgroup:
        return from_subgroup, "subgroup"
    return "", ""


def label_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    for row in rows:
        processing, source = label_food(row.get("food_name", ""), row.get("food_subgroup", ""))
        row["processing"] = processing
        row["processing_source"] = source
    return rows


def label_file(path: Path = DEFAULT_TABLE) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    for column in ("processing", "processing_source"):
        if column not in fieldnames:
            fieldnames.append(column)
    label_rows(rows)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return rows


def summarize(rows: list[dict[str, str]]) -> str:
    foods = [row for row in rows if row.get("row_type") == "food"]
    counts = Counter(row["processing"] or "(unlabeled)" for row in foods)
    labeled = sum(1 for row in foods if row["processing"])
    from_subgroup = sum(1 for row in foods if row["processing_source"] == "subgroup")
    lines = [
        f"foods {len(foods)}",
        f"labeled {labeled}",
        f"unlabeled {len(foods) - labeled}",
        f"from_subgroup {from_subgroup}",
        "labels:",
    ]
    for name, count in counts.most_common():
        lines.append(f"  {count:4}  {name}")
    return "\n".join(lines)


def _check_examples() -> None:
    cases = {
        "White bread, toasted (Hovis, UK)": "toasted",
        "Lower Carb Soy & Toasted Sesame Bread": "",
        "Instant oats, cooked in microwave for 2.5 min": "instant",
        "Basmati, parboiled long grain rice, Maharani brand": "parboiled",
        "Muesli, made from steamed rolled oats with dried fruit and nuts": "steamed",
        "Apple, dried": "dried",
        "New potato, unpeeled and boiled 20 min": "boiled",
        "Sweet potato, Dor cultivar, baked with skin on": "baked",
        "Baked Beans in Cheesy Tomato sauce": "canned",
        "White bread": "",
        "Green banana, peeled, fried in vegetable oil": "fried",
    }
    for text, expected in cases.items():
        got = label_text(text)
        if got != expected:
            raise AssertionError(f"{text!r}: expected {expected!r}, got {got!r}")
    got, source = label_food("Basmati rice (Dreamrice, Singapore)", "Basmati, white rice, boiled")
    if (got, source) != ("boiled", "subgroup"):
        raise AssertionError((got, source))


def main() -> None:
    _check_examples()
    rows = label_file()
    print(summarize(rows))
    print(f"wrote {DEFAULT_TABLE}")


if __name__ == "__main__":
    main()
