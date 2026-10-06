"""Shared data loading and statistics for the glycemic index notebook."""

from __future__ import annotations

import re
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from statsmodels.stats.multitest import multipletests

ROOT = Path(__file__).resolve().parents[1]
GI_TABLE = ROOT / "data" / "processed" / "gi_table1.csv"
USDA_MATCHES = ROOT / "data" / "processed" / "usda_matches.csv"

GROUP_LABELS = {
    "BAKERY PRODUCTS": "Bakery",
    "BEVERAGES": "Beverages",
    "BREADS": "Breads",
    "BREAKFAST CEREALS": "Breakfast cereals",
    "CEREAL GRAINS": "Cereal grains",
    "COOKIES": "Cookies",
    "CRACKERS": "Crackers",
    "DAIRY PRODUCTS AND ALTERNATIVES": "Dairy & alternatives",
    "FRUIT AND FRUIT PRODUCTS": "Fruit",
    "INFANT FORMULA AND WEANING FOODS": "Infant foods",
    "LEGUMES": "Legumes",
    "MEAL REPLACEMENT & WEIGHT MANAGEMENT PRODUCTS": "Meal replacements",
    "NUTRITIONAL SUPPORT PRODUCTS": "Nutritional support",
    "NUTS": "Nuts",
    "PASTA AND NOODLES": "Pasta & noodles",
    "SNACK FOODS AND CONFECTIONERY": "Snacks & confectionery",
    "SOUPS": "Soups",
    "SUGARS AND SYRUPS": "Sugars & syrups",
    "VEGETABLES": "Vegetables",
    "REGIONAL OR TRADITIONAL FOODS": "Regional & traditional",
}

GI_BANDS = [(0, 55, "Low (≤55)"), (56, 69, "Medium (56–69)"), (70, 200, "High (≥70)")]

# Within-food processing comparisons: (food group, name pattern, excluded pattern, methods).
# The first method is the reference every other method is compared against.
COMPARISONS = {
    "Potato": ("VEGETABLES", r"potato", r"sweet potato", ["boiled", "microwaved", "mashed", "baked", "instant"]),
    "Sweet potato": ("VEGETABLES", r"sweet potato|kumara", None, ["boiled", "fried", "roasted", "baked"]),
    "Rice": ("CEREAL GRAINS", r"\brice\b", None, ["boiled", "parboiled", "instant", "microwaved"]),
    "Oats": ("BREAKFAST CEREALS", r"\boat|porridge", None, ["boiled", "instant"]),
}


def gi_band(value: float) -> str:
    for low, high, label in GI_BANDS:
        if low <= value <= high:
            return label
    return ""


def load_foods() -> pd.DataFrame:
    """Individual food rows from Table 1, without the published group means."""
    table = pd.read_csv(GI_TABLE)
    foods = table[table["row_type"] == "food"].copy()
    foods["group"] = foods["food_group"].map(GROUP_LABELS)
    foods["gi_band"] = foods["gi"].map(gi_band)
    foods["serving_carb_g"] = foods["gl"] * 100 / foods["gi"]
    foods["processing"] = foods["processing"].fillna("")
    return foods.reset_index(drop=True)


def load_matches(status: str = "accepted") -> pd.DataFrame:
    matches = pd.read_csv(USDA_MATCHES)
    matches = matches[matches["match_status"] == status].copy()
    matches["group"] = matches["food_group"].map(GROUP_LABELS)
    matches["fdc_id"] = matches["fdc_id"].astype(int)
    return matches.reset_index(drop=True)


def comparison_foods(foods: pd.DataFrame) -> pd.DataFrame:
    """Foods that enter the within-food processing comparisons, tagged by commodity."""
    parts = []
    for commodity, (group, pattern, exclude, methods) in COMPARISONS.items():
        names = foods["food_name"]
        mask = (foods["food_group"] == group) & names.str.contains(pattern, case=False, regex=True)
        if exclude:
            mask &= ~names.str.contains(exclude, case=False, regex=True)
        mask &= foods["processing"].isin(methods)
        part = foods[mask].copy()
        part["commodity"] = commodity
        part["method"] = pd.Categorical(part["processing"], categories=methods, ordered=True)
        parts.append(part)
    return pd.concat(parts, ignore_index=True)


def cultivar(name: str) -> str:
    match = re.search(r"([A-Z][\w ]*?) cultivar", name)
    return match.group(1).strip() if match else ""


def cliffs_delta(x: np.ndarray, y: np.ndarray) -> float:
    """Probability x > y minus probability x < y, from -1 to 1."""
    x = np.asarray(x, dtype=float)[:, None]
    y = np.asarray(y, dtype=float)[None, :]
    return float(((x > y).sum() - (x < y).sum()) / (x.size * y.size))


def bootstrap_median_difference(
    x: np.ndarray, y: np.ndarray, reps: int = 5000, seed: int = 0
) -> tuple[float, float, float]:
    """Median of x minus median of y, with a 95% percentile bootstrap interval."""
    rng = np.random.default_rng(seed)
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    draws = np.median(rng.choice(x, (reps, x.size)), axis=1) - np.median(rng.choice(y, (reps, y.size)), axis=1)
    low, high = np.percentile(draws, [2.5, 97.5])
    return float(np.median(x) - np.median(y)), float(low), float(high)


def epsilon_squared(h: float, n: int) -> float:
    """Kruskal–Wallis effect size: the share of rank variance explained by group."""
    return h / ((n * n - 1) / (n + 1))


def dunn_test(data: pd.DataFrame, value: str, group: str) -> pd.DataFrame:
    """Dunn's pairwise rank test with tie correction and Holm adjustment."""
    data = data[[value, group]].dropna()
    ranks = data[value].rank()
    n = len(data)
    ties = data[value].value_counts()
    tie_term = ((ties**3 - ties).sum()) / (12 * (n - 1))
    variance = n * (n + 1) / 12 - tie_term
    summary = pd.DataFrame({"mean_rank": ranks.groupby(data[group]).mean(), "n": data.groupby(group).size()})
    rows = []
    for a, b in combinations(summary.index, 2):
        diff = summary.at[a, "mean_rank"] - summary.at[b, "mean_rank"]
        z = diff / np.sqrt(variance * (1 / summary.at[a, "n"] + 1 / summary.at[b, "n"]))
        rows.append({"group_a": a, "group_b": b, "z": z, "p": 2 * stats.norm.sf(abs(z))})
    result = pd.DataFrame(rows)
    result["p_holm"] = multipletests(result["p"], method="holm")[1]
    return result


def processing_contrasts(comparison: pd.DataFrame) -> pd.DataFrame:
    """Each method against the commodity's reference method, Holm-adjusted across all contrasts."""
    rows = []
    for commodity, (_, _, _, methods) in COMPARISONS.items():
        subset = comparison[comparison["commodity"] == commodity]
        reference = subset.loc[subset["processing"] == methods[0], "gi"].to_numpy()
        for method in methods[1:]:
            other = subset.loc[subset["processing"] == method, "gi"].to_numpy()
            diff, low, high = bootstrap_median_difference(other, reference)
            test = stats.mannwhitneyu(other, reference, alternative="two-sided")
            rows.append(
                {
                    "food": commodity,
                    "method": method,
                    "vs": methods[0],
                    "n_method": other.size,
                    "n_reference": reference.size,
                    "median_method": float(np.median(other)),
                    "median_reference": float(np.median(reference)),
                    "median_difference": diff,
                    "ci_low": low,
                    "ci_high": high,
                    "cliffs_delta": cliffs_delta(other, reference),
                    "p": test.pvalue,
                }
            )
    result = pd.DataFrame(rows)
    result["p_holm"] = multipletests(result["p"], method="holm")[1]
    return result


def carbohydrate_profiles(matches: pd.DataFrame) -> pd.DataFrame:
    """One row per matched USDA food, with the median GI of the foods matched to it.

    Many GI foods share one USDA entry (dozens of white breads all match
    "Bread, white wheat"), so the USDA food is the independent unit for
    composition questions.
    """
    first = {
        column: (column, "first")
        for column in [
            "usda_description",
            "group",
            "usda_carb_g",
            "usda_sugars_g",
            "usda_fiber_g",
            "usda_starch_g",
            "starch_source",
            "sugar_share",
            "fiber_share",
            "starch_share",
            "usda_glucose_g",
            "usda_fructose_g",
            "usda_sucrose_g",
            "usda_lactose_g",
            "usda_maltose_g",
            "usda_galactose_g",
        ]
    }
    profiles = matches.groupby("fdc_id").agg(gi=("gi", "median"), n_foods=("gi", "size"), **first).reset_index()
    sugars = profiles[["usda_glucose_g", "usda_fructose_g", "usda_sucrose_g", "usda_maltose_g"]]
    has_breakdown = sugars.notna().all(axis=1)
    lactose = profiles["usda_lactose_g"].fillna(0)
    galactose = profiles["usda_galactose_g"].fillna(0)
    carb = profiles["usda_carb_g"]
    # Sucrose is half glucose and half fructose; lactose is half glucose and half galactose.
    glucose_yield = (
        profiles["usda_starch_g"]
        + profiles["usda_glucose_g"]
        + profiles["usda_maltose_g"]
        + 0.5 * profiles["usda_sucrose_g"]
        + 0.5 * lactose
    ) / carb
    fructose_galactose = (profiles["usda_fructose_g"] + 0.5 * profiles["usda_sucrose_g"] + 0.5 * lactose + galactose) / carb
    profiles["glucose_yield_share"] = glucose_yield.where(has_breakdown)
    profiles["fructose_galactose_share"] = fructose_galactose.where(has_breakdown)
    return profiles
