"""CADP: Campaign Analytics Dashboard (Streamlit). Version 2.0.2 (see CHANGELOG.md).

Views: Campaign Overview, Channel Performance, Cost and ROI, Conversion Funnel,
Data Quality and Diagnostic Analysis (FR6, FR10). Sidebar filters apply to every
view (FR7). Each view has a record-level drill-down (FR8). All user input is sent
to the database as bound parameters; table and column names come only from the
database catalogue (NFR6).
"""
import time

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st
from scipy import stats
from sqlalchemy import bindparam, create_engine, text

APP_VERSION = "2.0.2"
st.set_page_config(page_title="CADP Campaign Analytics", layout="wide")

FACT = "fact_campaign_performance"
DIMS = ["dim_channel", "dim_campaign_type", "dim_segment", "dim_date"]
ACCENT = "#C55A11"
px.defaults.template = "plotly_white"
px.defaults.color_discrete_sequence = [ACCENT]

# Logical field -> column name in the dataset. Matched case-insensitively.
FIELDS = {
    "id": "Campaign_ID", "company": "Company", "type": "Campaign_Type",
    "audience": "Target_Audience", "duration": "Duration", "channel": "Channel_Used",
    "cr": "Conversion_Rate", "cost": "Acquisition_Cost", "roi": "ROI",
    "location": "Location", "language": "Language", "clicks": "Clicks",
    "impressions": "Impressions", "engagement": "Engagement_Score",
    "segment": "Customer_Segment", "date": "Date",
}
LABELS = {
    "channel": "Channel", "type": "Campaign type", "segment": "Customer segment",
    "company": "Company", "location": "Location", "language": "Language",
    "audience": "Target audience",
}


# ---------------------------------------------------------------- database
@st.cache_resource
def get_engine():
    return create_engine(st.secrets["DB_CONNECTION"], pool_pre_ping=True)


@st.cache_data(ttl=3600, show_spinner=False)
def catalogue():
    sql = text(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name IN :t ORDER BY ordinal_position"
    ).bindparams(bindparam("t", expanding=True))
    with get_engine().connect() as c:
        rows = c.execute(sql, {"t": [FACT] + DIMS}).fetchall()
    cols = {}
    for t, col in rows:
        cols.setdefault(t, []).append(col)
    return cols


def q(name):
    return '"' + name.replace('"', '""') + '"'


@st.cache_data(ttl=3600, show_spinner=False)
def base_query():
    """Builds the fact + dimension join from the catalogue, so the app does not
    depend on exact key names. Returns (sql, {logical field: column})."""
    cols = catalogue()
    if FACT not in cols:
        raise RuntimeError(f"Table {FACT} not found in the database.")
    fact_cols = cols[FACT]
    select, joins, seen = [], [], set()
    keys = set()
    for i, d in enumerate(DIMS):
        if d not in cols:
            continue
        shared = [c for c in cols[d] if c in fact_cols]
        if not shared:
            continue
        keys.update(shared)
        cond = " AND ".join(f"f.{q(k)} = d{i}.{q(k)}" for k in shared)
        joins.append(f"LEFT JOIN {q(d)} d{i} ON {cond}")
        for c in cols[d]:
            if c not in shared and c not in fact_cols and c.lower() not in seen:
                select.append(f"d{i}.{q(c)}")
                seen.add(c.lower())
    for c in fact_cols:
        if c not in keys and c.lower() not in seen:
            select.insert(0, f"f.{q(c)}")
            seen.add(c.lower())
    sql = f"SELECT {', '.join(select)} FROM {q(FACT)} f " + " ".join(joins)
    available = {s.split(".", 1)[1].strip('"').lower(): s.split(".", 1)[1].strip('"') for s in select}
    fmap = {k: available[v.lower()] for k, v in FIELDS.items() if v.lower() in available}
    return sql, fmap


def run(sql, params=None, expanding=()):
    """Runs a parameterised query and returns (DataFrame, seconds)."""
    key = (sql, tuple(sorted((k, tuple(v) if isinstance(v, list) else v) for k, v in (params or {}).items())))
    return _run(key, tuple(expanding))


@st.cache_data(ttl=600, show_spinner=False)
def _run(key, expanding):
    sql, items = key
    params = {k: list(v) if isinstance(v, tuple) else v for k, v in items}
    stmt = text(sql)
    if expanding:
        stmt = stmt.bindparams(*[bindparam(e, expanding=True) for e in expanding])
    t0 = time.perf_counter()
    with get_engine().connect() as c:
        df = pd.read_sql(stmt, c, params=params)
    return df, time.perf_counter() - t0


# ---------------------------------------------------------------- setup
try:
    BASE, F = base_query()
except Exception as e:  # shown instead of a stack trace
    st.error(f"Could not read the database structure: {e}")
    st.stop()

missing = [k for k in ("clicks", "impressions", "cr", "cost", "roi", "channel") if k not in F]
if missing:
    st.error(f"Columns not found in the database: {', '.join(FIELDS[m] for m in missing)}")
    st.stop()

C = {k: q(v) for k, v in F.items()}
MEASURES = f"""
    COUNT(*) AS campaigns,
    SUM({C['clicks']}) AS clicks,
    SUM({C['impressions']}) AS impressions,
    SUM({C['cost']}) AS spend,
    AVG({C['cr']}) AS avg_conversion_rate,
    AVG({C['roi']}) AS avg_roi,
    SUM({C['clicks']} * {C['cr']}) AS est_conversions,
    SUM({C['clicks']})::float / NULLIF(SUM({C['impressions']}), 0) AS ctr,
    SUM({C['cost']}) / NULLIF(SUM({C['clicks']} * {C['cr']}), 0) AS cost_per_conversion
"""

# ---------------------------------------------------------------- filters (FR7)
st.sidebar.header("Filters")
st.sidebar.caption("Leave a filter empty to include everything. Filters apply to every view.")


def owner(field):
    """The table that holds a column: a small dimension table where possible."""
    cols = catalogue()
    for t in DIMS + [FACT]:
        if F[field] in cols.get(t, []):
            return q(t)
    return f"({BASE}) b"


@st.cache_data(ttl=3600, show_spinner=False)
def options(field):
    df, _ = run(f"SELECT DISTINCT {C[field]} AS v FROM {owner(field)} WHERE {C[field]} IS NOT NULL ORDER BY 1")
    return df["v"].tolist()


where, params, expanding = [], {}, []
if "date" in F:
    rng, _ = run(f"SELECT MIN(CAST({C['date']} AS date)) AS lo, MAX(CAST({C['date']} AS date)) AS hi FROM {owner('date')}")
    lo, hi = pd.to_datetime(rng["lo"][0]).date(), pd.to_datetime(rng["hi"][0]).date()
    picked = st.sidebar.date_input("Time period", (lo, hi), min_value=lo, max_value=hi)
    if isinstance(picked, (list, tuple)) and len(picked) == 2 and tuple(picked) != (lo, hi):
        where.append(f"CAST({C['date']} AS date) BETWEEN :d_from AND :d_to")
        params.update(d_from=picked[0], d_to=picked[1])

for field in ["company", "location", "segment", "type", "channel", "language", "audience"]:
    if field not in F:
        continue
    chosen = st.sidebar.multiselect(LABELS[field], options(field))
    if chosen:
        name = f"f_{field}"
        where.append(f"{C[field]} IN :{name}")
        params[name] = [str(x) for x in chosen] if not isinstance(chosen[0], (int, np.integer)) else [int(x) for x in chosen]
        expanding.append(name)

WHERE = ("WHERE " + " AND ".join(where)) if where else ""
FILTERED = f"WITH base AS ({BASE}) SELECT * FROM base {WHERE}"
active = len(expanding) + (1 if "d_from" in params else 0)
st.sidebar.markdown(f"**Active filters:** {active}")


def agg(group=None, order=None):
    if group:
        sql = (f"WITH f AS ({FILTERED}) SELECT {C[group]} AS {q(group)}, {MEASURES} FROM f "
               f"GROUP BY 1 ORDER BY {order or 1}")
    else:
        sql = f"WITH f AS ({FILTERED}) SELECT {MEASURES} FROM f"
    return run(sql, params, expanding)


def money(v):
    return f"${v:,.0f}" if pd.notna(v) else "n/a"


def summary_table(df, group):
    """Readable KPI table: plain-English headings, formatted numbers, six columns."""
    return pd.DataFrame({
        LABELS.get(group, group): df[group],
        "Campaigns": df["campaigns"].map("{:,.0f}".format),
        "Clicks": df["clicks"].map("{:,.0f}".format),
        "Spend": df["spend"].map(money),
        "Conv. rate": df["avg_conversion_rate"].map("{:.2%}".format),
        "Avg ROI": df["avg_roi"].map("{:.2f}".format),
        "Cost per conv.": df["cost_per_conversion"].map("${:,.2f}".format),
    })


# ---------------------------------------------------------------- drill-down (FR8)
KPI_SORT = {"Clicks": "clicks", "Conversion rate": "cr", "ROI": "roi", "Acquisition cost": "cost"}


def drill(view, group, values):
    with st.expander("Drill down to campaign records", expanded=False):
        c1, c2 = st.columns(2)
        val = c1.selectbox(f"{LABELS.get(group, group)}", values, key=f"dv_{view}")
        sort_label = c2.selectbox("Sort records by", list(KPI_SORT), key=f"ds_{view}")
        p = dict(params, drill_val=val)
        cols = [k for k in ["id", "company", "channel", "type", "segment", "location", "date",
                            "clicks", "impressions", "cr", "cost", "roi"] if k in F]
        sel = ", ".join(f"{C[k]}" for k in cols)
        recs, _ = run(f"WITH f AS ({FILTERED}) SELECT {sel} FROM f WHERE {C[group]} = :drill_val "
                      f"ORDER BY {C[KPI_SORT[sort_label]]} DESC LIMIT 500", p, expanding)
        tot, _ = run(f"WITH f AS ({FILTERED}) SELECT COUNT(*) AS n, SUM({C['clicks']}) AS clicks, "
                     f"SUM({C['cost']}) AS spend FROM f WHERE {C[group]} = :drill_val", p, expanding)
        n, clicks, spend = int(tot["n"][0]), tot["clicks"][0], tot["spend"][0]
        st.caption(f"{n:,} campaigns match {LABELS.get(group, group).lower()} = {val} "
                   f"(clicks {clicks:,.0f}, spend {money(spend)}). These totals equal the values in the "
                   f"chart above. Showing the top {min(n, 500)} by {sort_label.lower()}.")
        st.dataframe(recs, width="stretch", hide_index=True)


# ---------------------------------------------------------------- layout
st.title("Campaign Analytics Dashboard")
st.caption("Synthetic marketing data (Kaggle, 200,000 campaigns). Conversions are estimated as clicks × "
           "conversion rate and credited to the campaign's single channel (campaign-level attribution).")

VIEWS = ["Campaign Overview", "Channel Performance", "Cost and ROI",
         "Conversion Funnel", "Data Quality", "Diagnostic Analysis"]
# Only the selected view queries the database (tabs would run all six on every load).
view = st.segmented_control("View", VIEWS, default=VIEWS[0], label_visibility="collapsed") or VIEWS[0]
timings = []

# 1. Overview
if view == VIEWS[0]:
    k, t = agg(); timings.append(t)
    r = k.iloc[0]
    if r["campaigns"] == 0:
        st.warning("No campaigns match the current filters.")
    else:
        m = st.columns(4)
        m[0].metric("Campaigns", f"{int(r['campaigns']):,}")
        m[1].metric("Total spend", money(r["spend"]))
        m[2].metric("Clicks", f"{r['clicks']:,.0f}")
        m[3].metric("Impressions", f"{r['impressions']:,.0f}")
        m = st.columns(4)
        m[0].metric("Avg conversion rate", f"{r['avg_conversion_rate']:.2%}")
        m[1].metric("Avg ROI", f"{r['avg_roi']:.2f}")
        m[2].metric("Est. conversions", f"{r['est_conversions']:,.0f}")
        m[3].metric("Click-through rate", f"{r['ctr']:.2%}")
        if "date" in F:
            mon, t = run(f"WITH f AS ({FILTERED}) SELECT date_trunc('month', CAST({C['date']} AS date))::date AS month, "
                         f"{MEASURES} FROM f GROUP BY 1 ORDER BY 1", params, expanding); timings.append(t)
            a, b = st.columns(2)
            a.plotly_chart(px.line(mon, x="month", y="clicks", markers=True, title="Clicks by month",
                                   labels={"month": "", "clicks": "Clicks"}), width="stretch")
            b.plotly_chart(px.line(mon, x="month", y="avg_conversion_rate", markers=True,
                                   title="Average conversion rate by month",
                                   labels={"month": "", "avg_conversion_rate": "Conversion rate"})
                           .update_yaxes(tickformat=".2%"), width="stretch")
        if "company" in F:
            comp, _ = agg("company", "clicks DESC")
            st.plotly_chart(px.bar(comp, x="company", y="spend", title="Spend by company",
                                   labels={"company": "", "spend": "Spend ($)"}), width="stretch")
            drill("overview", "company", comp["company"].tolist())

# 2. Channel performance
if view == VIEWS[1]:
    ch, t = agg("channel", "clicks DESC"); timings.append(t)
    a, b, c = st.columns(3)
    a.plotly_chart(px.bar(ch, x="channel", y="clicks", title="Clicks by channel",
                          labels={"channel": "", "clicks": "Clicks"}), width="stretch")
    b.plotly_chart(px.bar(ch, x="channel", y="avg_conversion_rate", title="Avg conversion rate by channel",
                          labels={"channel": "", "avg_conversion_rate": "Conversion rate"})
                   .update_yaxes(tickformat=".2%"), width="stretch")
    c.plotly_chart(px.bar(ch, x="channel", y="avg_roi", title="Avg ROI by channel",
                          labels={"channel": "", "avg_roi": "ROI"}), width="stretch")
    st.dataframe(summary_table(ch, "channel"), width="stretch", hide_index=True)
    drill("channel", "channel", ch["channel"].tolist())

# 3. Cost and ROI
if view == VIEWS[2]:
    grp = "type" if "type" in F else "channel"
    ct, t = agg(grp, "spend DESC"); timings.append(t)
    a, b = st.columns(2)
    a.plotly_chart(px.bar(ct, x=grp, y="spend", title=f"Spend by {LABELS[grp].lower()}",
                          labels={grp: "", "spend": "Spend ($)"}), width="stretch")
    b.plotly_chart(px.bar(ct, x=grp, y="cost_per_conversion",
                          title=f"Cost per estimated conversion by {LABELS[grp].lower()}",
                          labels={grp: "", "cost_per_conversion": "$ per conversion"}), width="stretch")
    bands, t = run(f"""WITH f AS ({FILTERED}), w AS (SELECT MIN({C['cost']}) AS lo, MAX({C['cost']}) AS hi FROM f)
        SELECT width_bucket({C['cost']}, w.lo, w.hi + 0.01, 10) AS band, MIN({C['cost']}) AS from_cost,
               MAX({C['cost']}) AS to_cost, AVG({C['roi']}) AS avg_roi, COUNT(*) AS campaigns
        FROM f, w GROUP BY 1 ORDER BY 1""", params, expanding); timings.append(t)
    corr, t = run(f"WITH f AS ({FILTERED}) SELECT CORR({C['cost']}, {C['roi']}) AS r, COUNT(*) AS n FROM f",
                  params, expanding); timings.append(t)
    if len(bands):
        bands["Cost band"] = bands.apply(lambda r: f"${r.from_cost/1000:,.0f}k to ${r.to_cost/1000:,.0f}k", axis=1)
        st.plotly_chart(px.bar(bands, x="Cost band", y="avg_roi", hover_data=["campaigns"],
                               title="Average ROI by acquisition cost band (ten equal-width bands)",
                               labels={"avg_roi": "Avg ROI", "Cost band": ""}), width="stretch")
        r_ = corr["r"].iloc[0]
        if pd.notna(r_):
            n_ = int(corr["n"].iloc[0])
            t_ = r_ * np.sqrt((n_ - 2) / max(1e-12, 1 - r_ ** 2))
            p_ = 2 * stats.t.sf(abs(t_), n_ - 2)
            st.caption(f"Correlation between acquisition cost and ROI across {n_:,} campaigns: "
                       f"r = {r_:.4f} (p = {p_:.3f}).")
    drill("cost", grp, ct[grp].tolist())

# 4. Conversion funnel
if view == VIEWS[3]:
    k, _ = agg(); r = k.iloc[0]
    if r["campaigns"] > 0:
        funnel = pd.DataFrame({"Stage": ["Impressions", "Clicks", "Est. conversions"],
                               "Count": [r["impressions"], r["clicks"], r["est_conversions"]]})
        a, b = st.columns([3, 2])
        funnel["Label"] = funnel["Count"].map(lambda v: f"{v / 1e9:.2f} billion" if v >= 1e9 else f"{v / 1e6:.1f} million")
        a.plotly_chart(px.funnel(funnel, x="Count", y="Stage", text="Label", title="Impressions to estimated conversions")
                       .update_traces(texttemplate="%{text}", textposition="outside"), width="stretch")
        b.metric("Click-through rate", f"{r['ctr']:.2%}")
        b.metric("Click to conversion", f"{r['est_conversions'] / r['clicks']:.2%}")
        b.metric("Impression to conversion", f"{r['est_conversions'] / r['impressions']:.3%}")
    if "segment" in F:
        sg, t = agg("segment", "est_conversions DESC"); timings.append(t)
        st.plotly_chart(px.bar(sg, x="segment", y="est_conversions", title="Estimated conversions by customer segment",
                               labels={"segment": "", "est_conversions": "Est. conversions"}), width="stretch")
        drill("funnel", "segment", sg["segment"].tolist())

# 5. Data quality (FR2, FR9)
if view == VIEWS[4]:
    st.caption("Checks run live against the database (refreshed every 10 minutes). They ignore the sidebar filters.")
    dup = f"COUNT(*) - COUNT(DISTINCT {C['id']})" if "id" in F else "0"
    dq, t = run(f"""
        SELECT
          (SELECT COUNT(*) FROM {q(FACT)}) AS fact_rows,
          COUNT(*) AS joined_rows,
          {dup} AS duplicate_ids,
          COUNT(*) FILTER (WHERE {C['channel']} IS NULL) AS unmatched_channel,
          COUNT(*) FILTER (WHERE {C['clicks']} > {C['impressions']}) AS clicks_gt_impr,
          COUNT(*) FILTER (WHERE {C['cr']} < 0 OR {C['cr']} > 1) AS cr_out_of_range,
          COUNT(*) FILTER (WHERE {C['cost']} IS NULL OR {C['clicks']} IS NULL OR {C['cr']} IS NULL) AS missing_values,
          COUNT(*) FILTER (WHERE {C['cost']} <= 0) AS non_positive_cost,
          SUM({C['clicks']}) AS clicks, SUM({C['impressions']}) AS impressions, SUM({C['cost']}) AS spend
        FROM ({BASE}) b"""); timings.append(t)
    d = dq.iloc[0]
    checks = [("Rows in fact table", d.fact_rows, d.fact_rows > 0),
              ("Rows lost when joining dimensions", d.fact_rows - d.joined_rows + d.unmatched_channel,
               d.fact_rows - d.joined_rows + d.unmatched_channel == 0)]
    if "id" in F:
        checks.append(("Duplicate Campaign_ID values", d.duplicate_ids, d.duplicate_ids == 0))
    checks += [("Clicks greater than impressions", d.clicks_gt_impr, d.clicks_gt_impr == 0),
               ("Conversion rate outside 0 to 1", d.cr_out_of_range, d.cr_out_of_range == 0),
               ("Missing cost, clicks or conversion rate", d.missing_values, d.missing_values == 0),
               ("Zero or negative acquisition cost", d.non_positive_cost, d.non_positive_cost == 0)]
    st.dataframe(pd.DataFrame([{"Check": n, "Result": f"{int(v):,}", "Status": "✔ Pass" if ok else "✖ Fail"}
                               for n, v, ok in checks]), width="stretch", hide_index=True)
    st.markdown("**Reconciliation totals** (compare with the source CSV, check P2)")
    st.dataframe(pd.DataFrame([{"Rows": f"{int(d.fact_rows):,}", "Clicks": f"{d.clicks:,.0f}",
                                "Impressions": f"{d.impressions:,.0f}", "Spend": money(d.spend)}]),
                 width="stretch", hide_index=True)

# 6. Diagnostic analysis (FR10)
# One-way ANOVA is computed from per-group count, mean and variance, which the
# database returns in a single GROUPING SETS query. No raw rows are downloaded.
if view == VIEWS[5]:
    st.markdown("Which campaign attributes are associated with higher conversion or ROI? Each attribute is "
                "tested with a one-way ANOVA. Eta squared (η²) is the share of variation the attribute explains.")
    outcome_label = st.radio("Outcome", ["Conversion rate", "ROI", "Engagement score"], horizontal=True)
    out = {"Conversion rate": "cr", "ROI": "roi", "Engagement score": "engagement"}[outcome_label]
    factors = [f for f in ["channel", "type", "segment", "company", "location", "language", "audience"] if f in F]
    if out not in F:
        st.info(f"{outcome_label} is not in the database.")
    else:
        y = C[out]
        sets = ", ".join(f"({C[f]})" for f in factors)
        flags = ", ".join(f"GROUPING({C[f]}) AS {q('g_' + f)}" for f in factors)
        labels = ", ".join(f"{C[f]}::text AS {q(f)}" for f in factors)
        grp, t = run(f"""WITH f AS ({FILTERED})
            SELECT {flags}, {labels}, COUNT({y}) AS n, AVG({y}) AS mean, VAR_SAMP({y}) AS var
            FROM f GROUP BY GROUPING SETS ({sets}, ())""", params, expanding); timings.append(t)
        total = grp[grp[[f"g_{f}" for f in factors]].eq(1).all(axis=1)]
        N = int(total["n"].iloc[0]) if len(total) else 0
        if N < 50:
            st.warning("Too few campaigns for analysis. Widen the filters.")
        else:
            grand = float(total["mean"].iloc[0])
            sst = float(total["var"].iloc[0]) * (N - 1)
            res = []
            for f in factors:
                g = grp[grp[f"g_{f}"] == 0][[f, "n", "mean", "var"]].dropna()
                g = g[g["n"] > 1]
                k = len(g)
                if k < 2:
                    continue
                ssb = float((g["n"] * (g["mean"] - grand) ** 2).sum())
                ssw = float(((g["n"] - 1) * g["var"]).sum())
                F_ = (ssb / (k - 1)) / (ssw / (N - k)) if ssw > 0 else float("inf")
                p_ = float(stats.f.sf(F_, k - 1, N - k))
                eta = ssb / sst if sst else 0.0
                best, worst = g.loc[g["mean"].idxmax()], g.loc[g["mean"].idxmin()]
                res.append({"Attribute": LABELS[f], "Groups": k, "F": round(F_, 2), "p-value": f"{p_:.4f}",
                            "η²": eta,
                            "Effect": "negligible" if eta < 0.01 else "small" if eta < 0.06 else "medium" if eta < 0.14 else "large",
                            "Best group": f"{best[f]} ({best['mean']:.4f})",
                            "Worst group": f"{worst[f]} ({worst['mean']:.4f})"})
            res = pd.DataFrame(res).sort_values("η²", ascending=False)
            st.dataframe(res, width="stretch", hide_index=True,
                         column_config={"η²": st.column_config.NumberColumn(format="%.5f")})
            top = res.iloc[0]
            sig = res[res["p-value"].astype(float) < 0.05]["Attribute"].tolist()
            st.markdown(
                f"**Interpretation.** Across {N:,} campaigns, the strongest single attribute for "
                f"{outcome_label.lower()} is {top['Attribute'].lower()} (η² = {top['η²']:.5f}, {top['Effect']} effect). "
                + (f"Statistically significant at p < 0.05: {', '.join(sig)}. " if sig else
                   "No attribute is statistically significant at p < 0.05. ")
                + ("No attribute explains even 1% of the variation, so in this dataset campaign attributes do not "
                   "explain performance and the dashboard should not recommend moving budget on these grounds."
                   if res["η²"].max() < 0.01 else
                   "Attributes with at least a small effect are worth testing further before changing budgets.")
            )
            st.markdown("**Two-attribute view**")
            a, b = st.columns(2)
            f1 = a.selectbox("Rows", factors, format_func=LABELS.get, index=0)
            f2 = b.selectbox("Columns", factors, format_func=LABELS.get, index=min(2, len(factors) - 1))
            if f1 != f2:
                two, t = run(f"WITH f AS ({FILTERED}) SELECT {C[f1]} AS r, {C[f2]} AS c, AVG({y}) AS mean, "
                             f"COUNT(*) AS n FROM f GROUP BY 1, 2", params, expanding); timings.append(t)
                piv = two.pivot(index="r", columns="c", values="mean")
                st.plotly_chart(px.imshow(piv, text_auto=".3f", aspect="auto", color_continuous_scale="Oranges",
                                          title=f"Mean {outcome_label.lower()} by {LABELS[f1].lower()} and {LABELS[f2].lower()}",
                                          labels={"color": outcome_label, "x": LABELS[f2], "y": LABELS[f1]}),
                                width="stretch")
                st.caption(f"Smallest cell has {int(two['n'].min()):,} campaigns. Spread between best and worst cell: "
                           f"{piv.max().max() - piv.min().min():.4f}.")
            else:
                st.info("Choose two different attributes.")

st.divider()
st.caption(f"Database time for this page: {sum(timings):.2f} s across {len(timings)} queries "
           f"(cached for 10 minutes). CADP dashboard v{APP_VERSION}. Source code: github.com/hemantashrestha/cadp-dashboard")
