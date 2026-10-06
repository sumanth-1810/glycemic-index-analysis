"""Parse Atkinson et al. 2021 Supplemental Table 1 into one row per food.

Source: Atkinson FS, Brand-Miller JC, Foster-Powell K, Buyken AE, Goletzke J.
International tables of glycemic index and glycemic load values 2021: a
systematic review. Am J Clin Nutr. 2021;114(5):1625-1632. doi:10.1093/ajcn/nqab233.
Supplemental Table 1 (ISO 26642:2010-consistent values).
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PDF = ROOT / "data" / "raw" / "atkinson2021_supplemental_table1.pdf"
DEFAULT_OUT = ROOT / "data" / "processed" / "gi_table1.csv"

# Horizontal bounds in the PDF text space. Food names sit on the left;
# the measured values sit in stable columns to the right.
COLUMNS = (
    ("food_number", 145, 230),
    ("food_name", 240, 1100),
    ("country", 1100, 1320),
    ("year", 1320, 1490),
    ("gi", 1490, 1680),
    ("gl", 1680, 1800),
    ("subjects", 1800, 2010),
    ("avail_carb_g", 2010, 2160),
    ("test_portion_g", 2160, 2320),
    ("reference_food", 2320, 2540),
    ("timepoints", 2540, 2760),
    ("sample_collection", 2760, 3030),
    ("analysis_method", 3030, 3220),
    ("reference", 3220, 4000),
)

LINE_TOLERANCE = 12

# Bold section titles in the PDF. Matched case-insensitively.
MAJOR_GROUPS = {
    "BAKERY PRODUCTS",
    "BEVERAGES",
    "BREADS",
    "BREAKFAST CEREALS",
    "CEREAL GRAINS",
    "COOKIES",
    "CRACKERS",
    "DAIRY PRODUCTS AND ALTERNATIVES",
    "FRUIT AND FRUIT PRODUCTS",
    "INFANT FORMULA AND WEANING FOODS",
    "LEGUMES",
    "MEAL REPLACEMENT & WEIGHT MANAGEMENT PRODUCTS",
    "NUTRITIONAL SUPPORT PRODUCTS",
    "NUTS",
    "PASTA AND NOODLES",
    "SNACK FOODS AND CONFECTIONERY",
    "SOUPS",
    "SUGARS AND SYRUPS",
    "VEGETABLES",
    "REGIONAL OR TRADITIONAL FOODS",
}

FIELDNAMES = [
    "food_number",
    "food_name",
    "food_group",
    "food_subgroup",
    "country",
    "year",
    "gi",
    "gi_sem",
    "gl",
    "subjects",
    "subject_n",
    "avail_carb_g",
    "test_portion_g",
    "reference_food",
    "timepoints",
    "sample_collection",
    "analysis_method",
    "reference_id",
    "group_avail_carb_portion_g",
    "row_type",
    "page",
]


def _join_tokens(tokens: list[tuple[float, str]]) -> str:
    """Join tokens on one line, repairing hyphens the extractor split apart."""
    parts = [text for _, text in sorted(tokens, key=lambda item: item[0])]
    out = ""
    index = 0
    while index < len(parts):
        part = parts[index]
        if part == "-":
            out = out.rstrip() + "-"
            index += 1
            continue
        nxt = parts[index + 1] if index + 1 < len(parts) else ""
        # A line-break hyphen often survives as a single letter plus the
        # rest of the word ("C" + "occo").
        if (
            part.isalpha()
            and len(part) <= 2
            and nxt[:1].islower()
        ):
            if out and not out.endswith("-") and not out.endswith("("):
                out += " "
            out += part + nxt
            index += 2
            continue
        if out.endswith("-") or out.endswith("(") or out == "":
            out += part
        elif part in {",", ")", "®", "™", "%", ":"}:
            out += part
        else:
            if not out.endswith(" "):
                out += " "
            out += part
        index += 1
    out = re.sub(r"\s+", " ", out).strip()
    out = re.sub(r"(\d)\s*o\s*C\b", r"\1°C", out)
    out = out.replace("W A ,", "WA,")
    return out


def _column_for(x: float) -> str | None:
    for name, left, right in COLUMNS:
        if left <= x < right:
            return name
    return None


def _font_style(font_name: str) -> str:
    lowered = font_name.lower()
    if "bold" in lowered:
        return "bold"
    if "italic" in lowered:
        return "italic"
    return "roman"


def _extract_lines(page) -> list[dict]:
    items: list[tuple[float, float, str, str]] = []

    def visitor(text, cm, tm, font_dict, font_size):
        token = text.replace("\n", "").strip()
        if token:
            font_name = (font_dict or {}).get("/BaseFont", "")
            items.append((tm[5], tm[4], token, _font_style(font_name)))

    page.extract_text(visitor_text=visitor)
    items.sort(key=lambda item: (-item[0], item[1]))

    grouped: list[list[tuple[float, float, str, str]]] = []
    for y, x, token, style in items:
        if not grouped or abs(grouped[-1][0][0] - y) > LINE_TOLERANCE:
            grouped.append([(y, x, token, style)])
        else:
            grouped[-1].append((y, x, token, style))

    lines = []
    for group in grouped:
        y = sum(item[0] for item in group) / len(group)
        buckets: dict[str, list[tuple[float, str]]] = {}
        for _, x, token, style in group:
            column = _column_for(x)
            if column is None:
                continue
            # Superscript footnote markers sit in the name column as bare
            # 1- or 2-digit tokens. Percents and years stay inside longer tokens.
            if column == "food_name" and re.fullmatch(r"\d{1,2}", token):
                continue
            buckets.setdefault(column, []).append((x, token))
        fields = {name: _join_tokens(tokens) for name, tokens in buckets.items()}
        number_tokens = buckets.get("food_number", [])
        digit_tokens = [text for _, text in sorted(number_tokens) if text.isdigit()]
        if digit_tokens:
            fields["food_number"] = "".join(digit_tokens)
        letter_styles = {
            style
            for _, token, style in (
                (x, token, style)
                for _, x, token, style in group
                if _column_for(x) == "food_name" and any(char.isalpha() for char in token)
            )
        }
        if "bold" in letter_styles and "roman" not in letter_styles:
            fields["style"] = "bold"
        elif letter_styles == {"italic"}:
            fields["style"] = "italic"
        else:
            fields["style"] = "roman"
        lines.append({"y": y, **fields})
    return lines


def _line_text(line: dict) -> str:
    return " ".join(
        line.get(name, "")
        for name, _, _ in COLUMNS
        if line.get(name)
    )


def _is_ignored(line: dict) -> bool:
    text = _line_text(line)
    markers = (
        "Atkinson FS",
        "Food Number and Item",
        "Supplemental Table",
        "Glycemic index (GI) values",
        "International tables",
    )
    return any(marker in text for marker in markers)


def _header_name(line: dict) -> str:
    return re.sub(r"\s+", " ", line.get("food_name", "")).strip(" .")


def _is_major_group(line: dict) -> bool:
    if line.get("style") != "bold" or line.get("food_number") or _has_measurements(line):
        return False
    return _header_name(line).upper() in MAJOR_GROUPS


def _is_subgroup(line: dict) -> bool:
    if line.get("style") != "bold" or line.get("food_number") or _has_measurements(line):
        return False
    name = _header_name(line)
    return bool(name) and name.upper() not in MAJOR_GROUPS


def _is_portion_note(line: dict) -> bool:
    return "carbohydrate portion" in line.get("food_name", "").lower()


def _is_mean(line: dict) -> bool:
    if line.get("food_number"):
        return False
    text = line.get("food_name", "").lower()
    return "mean of" in text or (line.get("style") == "italic" and "mean" in text)


def _has_measurements(line: dict) -> bool:
    return any(line.get(key) for key in ("gi", "gl", "country", "year", "subjects"))


def _parse_gi(value: str) -> tuple[str, str]:
    compact = value.replace(" ", "")
    matched = re.fullmatch(r"(\d+(?:\.\d+)?)±(\d+(?:\.\d+)?)", compact)
    if matched:
        return matched.group(1), matched.group(2)
    matched = re.fullmatch(r"(\d+(?:\.\d+)?)", compact)
    if matched:
        return matched.group(1), ""
    return "", ""


def _parse_number(value: str) -> str:
    value = value.replace(" ", "")
    if not value or value.upper() == "NS":
        return ""
    matched = re.fullmatch(r"\d+(?:\.\d+)?", value)
    return matched.group(0) if matched else ""


def _parse_subjects(value: str) -> tuple[str, str]:
    matched = re.search(r"(\d+)\s*$", value)
    count = matched.group(1) if matched else ""
    label = re.sub(r",?\s*\d+\s*$", "", value).strip(" ,")
    return label, count


def _strip_footnote(name: str) -> tuple[str, str]:
    matched = re.search(r"\)\s*(\d{1,2})$", name)
    if not matched:
        return name, ""
    return name[: matched.start()].rstrip() + ")", matched.group(1)


def _blank_record() -> dict[str, str]:
    return {key: "" for key in FIELDNAMES}


# Glycemic index and load sit on the food-number line. The column header
# repeats "GI ±" at the top of every page, so those two fields must not be
# copied off a wrapped line. Everything else can continue onto the next line.
WRAP_COLUMNS = {
    "food_name",
    "country",
    "year",
    "subjects",
    "avail_carb_g",
    "test_portion_g",
    "reference_food",
    "timepoints",
    "sample_collection",
    "analysis_method",
    "reference",
}


def _absorb(record: dict[str, str], line: dict, page_number: int, measurements: bool = True) -> None:
    for column, _, _ in COLUMNS:
        incoming = line.get(column, "")
        if not incoming or column == "food_number":
            continue
        if not measurements and column not in WRAP_COLUMNS:
            continue
        current = record.get(column, "")
        record[column] = f"{current} {incoming}".strip() if current else incoming
    if not record["page"]:
        record["page"] = str(page_number)


def parse_table(pdf_path: Path = DEFAULT_PDF) -> list[dict[str, str]]:
    reader = PdfReader(str(pdf_path))
    pages: list[list[dict]] = []
    for page in reader.pages:
        pages.append(_extract_lines(page))

    anchors: list[tuple[int, float, str]] = []
    for page_index, lines in enumerate(pages):
        for line in lines:
            number = line.get("food_number", "")
            if number.isdigit() and not _is_ignored(line):
                anchors.append((page_index, line["y"], number))

    def nearest_anchor(page_index: int, y: float) -> tuple[int, float, str] | None:
        best = None
        best_distance = None
        for anchor in anchors:
            if anchor[0] != page_index:
                continue
            distance = abs(anchor[1] - y)
            if best_distance is None or distance < best_distance:
                best = anchor
                best_distance = distance
        return best

    owned: dict[tuple[int, str], dict[str, str]] = {}
    for page_index, lines in enumerate(pages):
        for line in lines:
            if (
                _is_ignored(line)
                or _is_major_group(line)
                or _is_subgroup(line)
                or _is_portion_note(line)
                or _is_mean(line)
                or line.get("style") == "italic"
            ):
                continue
            number = line.get("food_number", "")
            if number.isdigit():
                anchor = (page_index, line["y"], number)
                measurements = True
            else:
                anchor = nearest_anchor(page_index, line["y"])
                if anchor is None or abs(anchor[1] - line["y"]) > 100:
                    continue
                measurements = False
            key = (anchor[0], anchor[2])
            record = owned.setdefault(key, _blank_record())
            record["food_number"] = anchor[2]
            record["row_type"] = "food"
            _absorb(record, line, page_index + 1, measurements)

    records: list[dict[str, str]] = []
    group = ""
    subgroup = ""
    portion = ""
    header_open = False
    for page_index, lines in enumerate(pages):
        for line in lines:
            if _is_ignored(line):
                continue
            if _is_portion_note(line):
                matched = re.search(r"(\d+(?:\.\d+)?)\s*g", line.get("food_name", ""))
                portion = matched.group(1) if matched else portion
                header_open = False
                continue
            if _is_major_group(line):
                group = _header_name(line).upper()
                subgroup = ""
                header_open = False
                continue
            if _is_subgroup(line):
                subgroup = _header_name(line)
                header_open = True
                continue
            if line.get("style") == "italic" and not _has_measurements(line) and header_open:
                # Wrapped tail of a bold subgroup heading, such as "countries".
                subgroup = f"{subgroup} {_header_name(line)}".strip()
                continue
            if _is_mean(line) or (
                line.get("style") == "italic"
                and records
                and records[-1]["row_type"] == "mean"
                and not _has_measurements(line)
            ):
                if (
                    records
                    and records[-1]["row_type"] == "mean"
                    and line.get("style") == "italic"
                    and not _is_mean(line)
                ):
                    records[-1]["food_name"] = f"{records[-1]['food_name']} {_header_name(line)}".strip()
                    continue
                record = _blank_record()
                record["row_type"] = "mean"
                record["food_group"] = group
                record["food_subgroup"] = subgroup
                record["group_avail_carb_portion_g"] = portion
                _absorb(record, line, page_index + 1)
                records.append(_finalize(record))
                continue
            number = line.get("food_number", "")
            if number.isdigit() and (page_index, number) in owned:
                record = owned[(page_index, number)]
                # A food number can be seen on its data line only once.
                if record.get("_emitted"):
                    continue
                record["food_group"] = group
                record["food_subgroup"] = subgroup
                record["group_avail_carb_portion_g"] = portion
                record["_emitted"] = "1"
                header_open = False
                records.append(_finalize(record))
                continue
    for record in records:
        record.pop("_emitted", None)
    return records


def _finalize(record: dict[str, str]) -> dict[str, str]:
    name, footnote = _strip_footnote(record.get("food_name", ""))
    record["food_name"] = name
    if footnote and not record.get("reference_id"):
        record["reference_id"] = footnote
    gi, sem = _parse_gi(record.get("gi", ""))
    record["gi"] = gi
    record["gi_sem"] = sem
    record["gl"] = _parse_number(record.get("gl", ""))
    record["avail_carb_g"] = _parse_number(record.get("avail_carb_g", ""))
    record["test_portion_g"] = _parse_number(record.get("test_portion_g", ""))
    label, count = _parse_subjects(record.get("subjects", ""))
    record["subjects"] = label
    record["subject_n"] = count
    record["year"] = re.sub(r"\s*-\s*", "-", record.get("year", "")).strip()
    record["country"] = re.sub(r"\s+", " ", record.get("country", "")).strip()
    reference = re.sub(r"UO\s+(\d+)", r"UO\1", record.get("reference", ""))
    record["reference_id"] = reference or record.get("reference_id", "")
    for key in FIELDNAMES:
        record.setdefault(key, "")
    return {key: record.get(key, "") for key in FIELDNAMES}


def write_csv(records: list[dict[str, str]], path: Path = DEFAULT_OUT) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(records)


def summarize(records: list[dict[str, str]]) -> str:
    foods = [row for row in records if row["row_type"] == "food"]
    numbers = [int(row["food_number"]) for row in foods if row["food_number"].isdigit()]
    present = set(numbers)
    missing = [number for number in range(1, max(numbers) + 1) if number not in present] if numbers else []
    with_gi = sum(1 for row in foods if row["gi"])
    with_name = sum(1 for row in foods if row["food_name"])
    groups: dict[str, int] = {}
    for row in foods:
        groups[row["food_group"]] = groups.get(row["food_group"], 0) + 1
    lines = [
        f"rows {len(records)}",
        f"foods {len(foods)}",
        f"means {sum(1 for row in records if row['row_type'] == 'mean')}",
        f"with_name {with_name}",
        f"with_gi {with_gi}",
        f"number_min {min(numbers) if numbers else None}",
        f"number_max {max(numbers) if numbers else None}",
        f"missing_numbers {missing}",
        "groups:",
    ]
    for name, count in groups.items():
        lines.append(f"  {count:4}  {name}")
    return "\n".join(lines)


def main() -> None:
    records = parse_table()
    write_csv(records)
    print(summarize(records))
    from label_processing import label_file, summarize as summarize_processing

    labeled = label_file(DEFAULT_OUT)
    print(summarize_processing(labeled))
    print(f"wrote {DEFAULT_OUT}")


if __name__ == "__main__":
    main()
