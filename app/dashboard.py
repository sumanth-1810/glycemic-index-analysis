"""Interactive dashboard for the glycemic index analysis.

Run with: streamlit run app/dashboard.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import analysis as A  # noqa: E402

st.set_page_config(page_title="Glycemic Index Explorer", page_icon="🍚", layout="wide")

BAND_COLORS = {"Low (≤55)": "#55A868", "Medium (56–69)": "#DD8452", "High (≥70)": "#C44E52"}
BAND_ORDER = list(BAND_COLORS)
GROUP_COLORS = dict(zip(sorted(A.GROUP_LABELS.values()), px.colors.qualitative.Dark24))


@st.cache_data
def load():
    foods = A.load_foods()
    matches = A.load_matches()
    profiles = A.carbohydrate_profiles(matches)
    comparison = A.comparison_foods(foods)
    comparison["method"] = comparison["method"].astype(str)
    return foods, matches, profiles, comparison


def band_lines(fig: go.Figure, axis: str = "y") -> go.Figure:
    for edge in (55.5, 69.5):
        if axis == "y":
            fig.add_hline(y=edge, line_dash="dash", line_color="grey", line_width=1)
        else:
            fig.add_vline(x=edge, line_dash="dash", line_color="grey", line_width=1)
    return fig


def p_text(p: float) -> str:
    return f"{p:.1e}" if p < 0.001 else f"{p:.3f}"


foods, matches, profiles, comparison = load()

# ---------------------------------------------------------------- sidebar filters
st.sidebar.header("Filter foods")
st.sidebar.caption("Filters apply to the Explorer, Food groups and Glycemic load tabs.")
search = st.sidebar.text_input("Search food names", placeholder="e.g. basmati, porridge, banana")
groups = st.sidebar.multiselect("Food groups", sorted(foods["group"].unique()))
bands = st.sidebar.multiselect("GI band", BAND_ORDER)
methods = st.sidebar.multiselect("Processing method", sorted(m for m in foods["processing"].unique() if m))
countries = st.sidebar.multiselect("Country tested", foods["country"].value_counts().index.tolist())
gi_range = st.sidebar.slider("GI range", 0, 120, (0, 120))

view = foods
if search:
    view = view[view["food_name"].str.contains(search, case=False, regex=False)]
if groups:
    view = view[view["group"].isin(groups)]
if bands:
    view = view[view["gi_band"].isin(bands)]
if methods:
    view = view[view["processing"].isin(methods)]
if countries:
    view = view[view["country"].isin(countries)]
view = view[view["gi"].between(*gi_range)]

st.sidebar.markdown(f"**{len(view):,}** of {len(foods):,} foods selected")
st.sidebar.markdown(
    "---\nData: Atkinson et al., *Am J Clin Nutr* 2021, Supplemental Table 1; "
    "USDA FoodData Central SR Legacy (2018)."
)

# ---------------------------------------------------------------- header
st.title("Glycemic Index & Dietary Impact Explorer")
st.markdown(
    "How food group, processing and carbohydrate type relate to **glycemic index (GI)** and "
    "**glycemic load (GL)** across 2,091 foods from the 2021 international GI tables. "
    "GI bands: low ≤ 55, medium 56–69, high ≥ 70. GL = GI × carbohydrate per serving ÷ 100."
)

if view.empty:
    st.warning("No foods match the current filters.")
    st.stop()

explorer, group_tab, processing_tab, carb_tab, gl_tab = st.tabs(
    ["Food explorer", "Food groups", "Processing", "Carbohydrate type", "Glycemic load"]
)

# ---------------------------------------------------------------- explorer
with explorer:
    cols = st.columns(4)
    cols[0].metric("Foods", f"{len(view):,}")
    cols[1].metric("Median GI", f"{view['gi'].median():.0f}")
    cols[2].metric("Median GL per serving", f"{view['gl'].median():.0f}")
    cols[3].metric("Low-GI share", f"{(view['gi'] <= 55).mean():.0%}")

    left, right = st.columns([3, 2])
    with left:
        fig = px.scatter(
            view.dropna(subset=["gl"]),
            x="gi",
            y="gl",
            color="group",
            color_discrete_map=GROUP_COLORS,
            hover_name="food_name",
            hover_data={"country": True, "processing": True, "serving_carb_g": ":.0f", "group": False},
            labels={"gi": "Glycemic index", "gl": "Glycemic load per serving", "serving_carb_g": "Carbs per serving (g)"},
            title="Every food: GI against GL (hover for details)",
            opacity=0.7,
            height=520,
        )
        band_lines(fig, axis="x")
        fig.update_traces(marker_size=7)
        fig.update_layout(legend_title_text="")
        st.plotly_chart(fig, width="stretch")
    with right:
        fig = px.histogram(
            view,
            x="gi",
            color="gi_band",
            color_discrete_map=BAND_COLORS,
            category_orders={"gi_band": BAND_ORDER},
            nbins=40,
            labels={"gi": "Glycemic index", "gi_band": "GI band"},
            title="GI distribution",
            height=520,
        )
        fig.update_layout(bargap=0.05, legend=dict(orientation="h", y=-0.2))
        st.plotly_chart(fig, width="stretch")

    st.subheader("Food table")
    st.dataframe(
        view[["food_name", "group", "processing", "gi", "gi_sem", "gl", "serving_carb_g", "country", "year"]]
        .sort_values("gi", ascending=False),
        hide_index=True,
        width="stretch",
        column_config={
            "food_name": st.column_config.TextColumn("Food", width="large"),
            "group": "Group",
            "processing": "Processing",
            "gi": st.column_config.ProgressColumn("GI", min_value=0, max_value=120, format="%d"),
            "gi_sem": st.column_config.NumberColumn("± SEM", format="%.0f"),
            "gl": st.column_config.NumberColumn("GL", format="%.0f"),
            "serving_carb_g": st.column_config.NumberColumn("Carbs / serving (g)", format="%.0f"),
            "country": "Country",
            "year": "Year",
        },
        height=420,
    )

# ---------------------------------------------------------------- food groups
with group_tab:
    counts = view["group"].value_counts()
    usable = counts[counts >= 3].index
    subset = view[view["group"].isin(usable)]
    order = subset.groupby("group")["gi"].median().sort_values().index.tolist()

    if len(usable) >= 2:
        kw = stats.kruskal(*[g["gi"] for _, g in subset.groupby("group")])
        eps2 = A.epsilon_squared(kw.statistic, len(subset))
        cols = st.columns(3)
        cols[0].metric("Kruskal–Wallis H", f"{kw.statistic:.1f}")
        cols[1].metric("p-value", p_text(kw.pvalue))
        cols[2].metric("Effect size ε²", f"{eps2:.2f}", help="Share of rank variance in GI explained by food group")

    fig = px.box(
        subset,
        y="group",
        x="gi",
        points="all",
        color="group",
        color_discrete_map=GROUP_COLORS,
        category_orders={"group": order},
        hover_name="food_name",
        labels={"gi": "Glycemic index", "group": ""},
        title="GI by food group, ordered by median (each dot is one food)",
        height=max(400, 34 * len(order)),
    )
    band_lines(fig, axis="x")
    fig.update_traces(marker_size=4, jitter=0.5, pointpos=0, boxmean=False)
    fig.update_layout(showlegend=False)
    st.plotly_chart(fig, width="stretch")

    if len(usable) >= 2:
        st.subheader("Which pairs differ? (Dunn test, Holm-corrected)")
        dunn = A.dunn_test(subset, "gi", "group")
        medians = subset.groupby("group")["gi"].median()
        matrix = pd.DataFrame(np.nan, index=order, columns=order)
        for row in dunn.itertuples():
            if row.p_holm < 0.05:
                matrix.loc[row.group_a, row.group_b] = medians[row.group_a] - medians[row.group_b]
                matrix.loc[row.group_b, row.group_a] = medians[row.group_b] - medians[row.group_a]
        limit = float(np.nanmax(np.abs(matrix.to_numpy()))) if matrix.notna().any().any() else 1.0
        fig = px.imshow(
            matrix,
            text_auto=".0f",
            color_continuous_scale="RdBu_r",
            zmin=-limit,
            zmax=limit,
            aspect="auto",
            labels={"color": "Median GI<br>row − column"},
            height=max(450, 30 * len(order)),
        )
        st.plotly_chart(fig, width="stretch")
        st.caption(
            f"{(dunn['p_holm'] < 0.05).sum()} of {len(dunn)} pairs differ after Holm correction. "
            "Blank cells are not significant."
        )

# ---------------------------------------------------------------- processing
with processing_tab:
    st.markdown(
        "Processing is compared **within the same food**, against boiling. Comparing methods across the "
        "whole table would mostly compare foods: baked items are mostly potatoes, canned items mostly legumes."
    )
    commodity = st.radio("Food", list(A.COMPARISONS), horizontal=True)
    food_methods = A.COMPARISONS[commodity][3]
    subset = comparison[comparison["commodity"] == commodity]

    fig = px.box(
        subset,
        x="method",
        y="gi",
        points="all",
        color="method",
        category_orders={"method": food_methods},
        hover_name="food_name",
        labels={"gi": "Glycemic index", "method": ""},
        title=f"{commodity}: GI by processing method (hover a dot for the food)",
        height=440,
    )
    band_lines(fig)
    fig.update_traces(marker_size=6, jitter=0.4, pointpos=0)
    fig.update_layout(showlegend=False)
    st.plotly_chart(fig, width="stretch")

    contrasts = A.processing_contrasts(comparison)
    table = contrasts[contrasts["food"] == commodity].copy()
    table["ci"] = table.apply(lambda r: f"{r.ci_low:+.0f} to {r.ci_high:+.0f}", axis=1)
    st.markdown("**Each method vs boiled** (Mann–Whitney, Holm-corrected across all 11 contrasts)")
    st.dataframe(
        table[["method", "n_method", "median_method", "median_reference", "median_difference", "ci",
               "cliffs_delta", "p_holm"]],
        hide_index=True,
        width="stretch",
        column_config={
            "method": "Method",
            "n_method": "n",
            "median_method": st.column_config.NumberColumn("Median GI", format="%.0f"),
            "median_reference": st.column_config.NumberColumn("Boiled median", format="%.0f"),
            "median_difference": st.column_config.NumberColumn("Difference", format="%+.0f"),
            "ci": "95% CI",
            "cliffs_delta": st.column_config.NumberColumn("Cliff's δ", format="%+.2f"),
            "p_holm": st.column_config.NumberColumn("p (Holm)", format="%.4f"),
        },
    )
    st.caption("Cliff's δ: chance a food cooked this way has higher GI than a boiled one, minus the reverse.")

    if commodity == "Sweet potato":
        st.subheader("Matched experiment: ten cultivars, four cooking methods")
        sweet = subset.copy()
        sweet["cultivar"] = sweet["food_name"].map(A.cultivar)
        paired = sweet[sweet["cultivar"] != ""].pivot_table(index="cultivar", columns="method", values="gi")
        paired = paired[["boiled", "fried", "baked", "roasted"]].dropna()
        long = paired.reset_index().melt(id_vars="cultivar", var_name="method", value_name="gi")
        fig = px.line(
            long,
            x="method",
            y="gi",
            color="cultivar",
            markers=True,
            labels={"gi": "Glycemic index", "method": ""},
            height=450,
        )
        band_lines(fig)
        friedman = stats.friedmanchisquare(*[paired[m] for m in paired.columns])
        left, right = st.columns([3, 2])
        left.plotly_chart(fig, width="stretch")
        with right:
            st.metric("Friedman test p-value", p_text(friedman.pvalue))
            for method in ["fried", "baked", "roasted"]:
                gap = paired[method] - paired["boiled"]
                st.metric(
                    f"{method.capitalize()} vs boiled",
                    f"+{gap.median():.0f} GI",
                    f"higher in {(gap > 0).sum()}/{len(gap)} cultivars",
                    delta_color="off",
                )

# ---------------------------------------------------------------- carbohydrate type
with carb_tab:
    st.markdown(
        f"{len(matches)} GI foods were matched to **{len(profiles)} distinct USDA foods**. Each dot is one USDA "
        "food, with the median GI of the foods matched to it; dot size is how many GI foods matched."
    )
    measures = {
        "Starch share of carbohydrate": "starch_share",
        "Sugar share of carbohydrate": "sugar_share",
        "Fiber share of carbohydrate": "fiber_share",
        "Share that digests to glucose": "glucose_yield_share",
        "Fructose + galactose share": "fructose_galactose_share",
    }
    choice = st.selectbox("Composition measure", list(measures))
    column = measures[choice]
    data = profiles.dropna(subset=[column])
    rho = stats.spearmanr(data[column], data["gi"])

    left, right = st.columns([3, 1])
    with left:
        fig = px.scatter(
            data,
            x=column,
            y="gi",
            color="group",
            size="n_foods",
            size_max=28,
            color_discrete_map=GROUP_COLORS,
            hover_name="usda_description",
            hover_data={"n_foods": True, "starch_source": True, column: ":.2f", "group": False},
            trendline="ols",
            trendline_scope="overall",
            trendline_color_override="black",
            labels={column: choice, "gi": "Glycemic index", "n_foods": "GI foods matched"},
            height=540,
        )
        band_lines(fig)
        fig.update_layout(legend_title_text="")
        st.plotly_chart(fig, width="stretch")
    with right:
        st.metric("Spearman ρ", f"{rho.statistic:+.2f}")
        st.metric("p-value", p_text(rho.pvalue))
        st.metric("USDA foods", len(data))
        if column in ("glucose_yield_share", "fructose_galactose_share"):
            st.caption(
                "Glucose-yielding = starch, glucose and maltose, plus half of sucrose and lactose. "
                "Only foods with a USDA sugar breakdown are shown."
            )

    st.subheader("Sweeteners: all sugar, very different GI")
    sweeteners = profiles[profiles["group"] == "Sugars & syrups"].sort_values("gi")
    fig = px.bar(
        sweeteners,
        x="gi",
        y="usda_description",
        orientation="h",
        text="gi",
        color="gi",
        color_continuous_scale="RdYlGn_r",
        range_color=(40, 100),
        labels={"gi": "Glycemic index", "usda_description": ""},
        height=300,
    )
    fig.update_layout(coloraxis_showscale=False)
    st.plotly_chart(fig, width="stretch")
    st.caption(
        "High-fructose corn syrup is about half fructose; malt syrup is mostly maltose (two glucose units). "
        "Which sugar matters more than how much sugar."
    )

# ---------------------------------------------------------------- glycemic load
with gl_tab:
    gl = view.dropna(subset=["gl", "serving_carb_g"])
    left, right = st.columns(2)
    with left:
        fig = px.scatter(
            gl,
            x="gi",
            y="gl",
            color="serving_carb_g",
            color_continuous_scale="Viridis",
            hover_name="food_name",
            hover_data={"group": True, "serving_carb_g": ":.0f"},
            labels={"gi": "Glycemic index", "gl": "Glycemic load per serving", "serving_carb_g": "Carbs / serving (g)"},
            title="Same GI, very different GL",
            height=500,
        )
        for edge in (10.5, 19.5):
            fig.add_hline(y=edge, line_dash="dot", line_color="grey", line_width=1)
        st.plotly_chart(fig, width="stretch")
    with right:
        rank = pd.DataFrame({
            "gi": gl.groupby("group")["gi"].median(),
            "gl": gl.groupby("group")["gl"].median(),
        })
        rank["gi_rank"] = rank["gi"].rank(ascending=False, method="first")
        rank["gl_rank"] = rank["gl"].rank(ascending=False, method="first")
        rank = rank.reset_index()
        fig = go.Figure()
        for row in rank.itertuples():
            fig.add_trace(go.Scatter(
                x=[0, 1],
                y=[row.gi_rank, row.gl_rank],
                mode="lines+markers+text",
                text=[row.group, row.group],
                textposition=["middle left", "middle right"],
                textfont_size=10,
                line=dict(color=GROUP_COLORS.get(row.group, "grey")),
                hovertemplate=f"{row.group}<br>median GI {row.gi:.0f}, median GL {row.gl:.0f}<extra></extra>",
                showlegend=False,
            ))
        fig.update_yaxes(autorange="reversed", title="Rank (1 = highest)")
        fig.update_xaxes(range=[-0.8, 1.8], tickvals=[0, 1], ticktext=["Rank by GI", "Rank by GL"], showgrid=False)
        fig.update_layout(title="Food groups reorder by GL", height=500)
        st.plotly_chart(fig, width="stretch")

    st.subheader("Portion calculator")
    st.markdown("Pick a food and set how much carbohydrate you eat to see the glycemic load of your portion.")
    options = gl.sort_values("food_name")
    common = options.index[options["food_name"].str.startswith("White bread")].tolist()
    default = options.index.get_loc(common[0]) if common else 0
    pick = st.selectbox("Food", options.index, index=default, format_func=lambda i: f"{options.at[i, 'food_name'][:90]} (GI {options.at[i, 'gi']:.0f})")
    food = options.loc[pick]
    grams = st.slider("Available carbohydrate in your portion (g)", 5, 120, int(round(food["serving_carb_g"])))
    portion_gl = food["gi"] * grams / 100
    level = "low" if portion_gl <= 10 else "medium" if portion_gl < 20 else "high"
    cols = st.columns(3)
    cols[0].metric("GI", f"{food['gi']:.0f}")
    cols[1].metric("Your GL", f"{portion_gl:.1f}", f"{level} (≤10 low, ≥20 high)", delta_color="off")
    cols[2].metric("Table serving", f"{food['serving_carb_g']:.0f} g carbs → GL {food['gl']:.0f}")
