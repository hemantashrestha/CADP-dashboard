"""CADP: Campaign Analytics Dashboard (Streamlit). Version 2.0.0 (see CHANGELOG.md).

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

APP_VERSION = "2.0.0"
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


@st.cache_data(ttl=3600, show_spinner=False)
def options(field):
    df, _ = run(f"WITH base AS ({BASE}) SELECT DISTINCT {C[field]} AS v FROM base WHERE {C[field]} IS NOT NULL ORDER BY 1")
    return df["v"].tolist()


where, params, expanding = [], {}, []
if "date" in F:
    rng, _ = run(f"WITH base AS ({BASE}) SELECT MIN(CAST({C['date']} AS date)) AS lo, MAX(CAST({C['date']} AS date)) AS hi FROM base")
    lo, hi = pd.to_datetime(rng["lo"][0]).date(), pd.to_datetime(rng["hi"][0]).date()
    picked = st.sidebar.date_input("Time period", (lo, hi), min_value=lo, max_value=hi)
    if isinstance(picked, (list, tuple)) and len(picked) == 2:
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
active = len(expanding) + (1 if "d_from" in params and (params["d_from"], params["d_to"]) != (lo, hi) else 0)
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

tabs = st.tabs(["Campaign Overview", "Channel Performance", "Cost and ROI",
                "Conversion Funnel", "Data Quality", "Diagnostic Analysis"])
timings = []

# 1. Overview
with tabs[0]:
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
                           .update_yaxes(tickformat=".1%"), width="stretch")
        if "company" in F:
            comp, _ = agg("company", "clicks DESC")
            st.plotly_chart(px.bar(comp, x="company", y="spend", title="Spend by company",
                                   labels={"company": "", "spend": "Spend ($)"}), width="stretch")
            drill("overview", "company", comp["company"].tolist())

# 2. Channel performance
with tabs[1]:
    ch, t = agg("channel", "clicks DESC"); timings.append(t)
    a, b, c = st.columns(3)
    a.plotly_chart(px.bar(ch, x="channel", y="clicks", title="Clicks by channel",
                          labels={"channel": "", "clicks": "Clicks"}), width="stretch")
    b.plotly_chart(px.bar(ch, x="channel", y="avg_conversion_rate", title="Avg conversion rate by channel",
                          labels={"channel": "", "avg_conversion_rate": "Conversion rate"})
                   .update_yaxes(tickformat=".1%"), width="stretch")
    c.plotly_chart(px.bar(ch, x="channel", y="avg_roi", title="Avg ROI by channel",
                          labels={"channel": "", "avg_roi": "ROI"}), width="stretch")
    st.dataframe(ch.rename(columns={"channel": "Channel"}), width="stretch", hide_index=True)
    drill("channel", "channel", ch["channel"].tolist())

# 3. Cost and ROI
with tabs[2]:
    grp = "type" if "type" in F else "channel"
    ct, t = agg(grp, "spend DESC"); timings.append(t)
    a, b = st.columns(2)
    a.plotly_chart(px.bar(ct, x=grp, y="spend", title=f"Spend by {LABELS[grp].lower()}",
                          labels={grp: "", "spend": "Spend ($)"}), width="stretch")
    b.plotly_chart(px.bar(ct, x=grp, y="cost_per_conversion",
                          title=f"Cost per estimated conversion by {LABELS[grp].lower()}",
                          labels={grp: "", "cost_per_conversion": "$ per conversion"}), width="stretch")
    sample, t = run(f"WITH f AS ({FILTERED}) SELECT {C['cost']} AS cost, {C['roi']} AS roi, {C[grp]} AS grp "
                    f"FROM f ORDER BY random() LIMIT 3000", params, expanding); timings.append(t)
    st.plotly_chart(px.scatter(sample, x="cost", y="roi", opacity=0.35,
                               title="Acquisition cost against ROI (random sample of 3,000 campaigns)",
                               labels={"cost": "Acquisition cost ($)", "roi": "ROI"}), width="stretch")
    if len(sample) > 2:
        r_, p_ = stats.pearsonr(sample["cost"], sample["roi"])
        st.caption(f"Correlation between cost and ROI in this sample: r = {r_:.3f} (p = {p_:.3f}).")
    drill("cost", grp, ct[grp].tolist())

# 4. Conversion funnel
with tabs[3]:
    k, _ = agg(); r = k.iloc[0]
    if r["campaigns"] > 0:
        funnel = pd.DataFrame({"Stage": ["Impressions", "Clicks", "Est. conversions"],
                               "Count": [r["impressions"], r["clicks"], r["est_conversions"]]})
        a, b = st.columns([3, 2])
        a.plotly_chart(px.funnel(funnel, x="Count", y="Stage", title="Impressions to estimated conversions"),
                       width="stretch")
        b.metric("Click-through rate", f"{r['ctr']:.2%}")
        b.metric("Click to conversion", f"{r['est_conversions'] / r['clicks']:.2%}")
        b.metric("Impression to conversion", f"{r['est_conversions'] / r['impressions']:.3%}")
    if "segment" in F:
        sg, t = agg("segment", "est_conversions DESC"); timings.append(t)
        st.plotly_chart(px.bar(sg, x="segment", y="est_conversions", title="Estimated conversions by customer segment",
                               labels={"segment": "", "est_conversions": "Est. conversions"}), width="stretch")
        drill("funnel", "segment", sg["segment"].tolist())

# 5. Data quality (FR2, FR9)
with tabs[4]:
    st.caption("Checks run live against the database on every load. They ignore the sidebar filters.")
    checks = [
        ("Rows in fact table", f"SELECT COUNT(*) FROM {q(FACT)}", lambda v: v > 0),
        ("Rows lost when joining dimensions", f"SELECT (SELECT COUNT(*) FROM {q(FACT)}) - COUNT(*) FROM ({BASE}) b "
         f"WHERE {C['channel']} IS NOT NULL", lambda v: v == 0),
        ("Clicks greater than impressions", f"SELECT COUNT(*) FROM ({BASE}) b WHERE {C['clicks']} > {C['impressions']}",
         lambda v: v == 0),
        ("Conversion rate outside 0 to 1", f"SELECT COUNT(*) FROM ({BASE}) b WHERE {C['cr']} < 0 OR {C['cr']} > 1",
         lambda v: v == 0),
        ("Missing cost, clicks or conversion rate", f"SELECT COUNT(*) FROM ({BASE}) b WHERE {C['cost']} IS NULL "
         f"OR {C['clicks']} IS NULL OR {C['cr']} IS NULL", lambda v: v == 0),
        ("Zero or negative acquisition cost", f"SELECT COUNT(*) FROM ({BASE}) b WHERE {C['cost']} <= 0", lambda v: v == 0),
    ]
    if "id" in F:
        checks.insert(1, ("Duplicate Campaign_ID values",
                          f"SELECT COUNT(*) - COUNT(DISTINCT {C['id']}) FROM ({BASE}) b", lambda v: v == 0))
    rows = []
    for name, sql, ok in checks:
        df, t = run(sql); v = df.iloc[0, 0]; timings.append(t)
        rows.append({"Check": name, "Result": f"{v:,}", "Status": "✔ Pass" if ok(v) else "✖ Fail"})
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    tot, _ = run(f"SELECT COUNT(*) AS rows, SUM({C['clicks']}) AS clicks, SUM({C['impressions']}) AS impressions, "
                 f"SUM({C['cost']}) AS spend FROM ({BASE}) b")
    st.markdown("**Reconciliation totals** (compare with the source CSV, check P2)")
    st.dataframe(tot, width="stretch", hide_index=True)

# 6. Diagnostic analysis (FR10)
with tabs[5]:
    st.markdown("Which campaign attributes are associated with higher conversion or ROI? Each attribute is "
                "tested with a one-way ANOVA. Eta squared (η²) is the share of variation the attribute explains.")
    outcome_label = st.radio("Outcome", ["Conversion rate", "ROI", "Engagement score"], horizontal=True)
    out = {"Conversion rate": "cr", "ROI": "roi", "Engagement score": "engagement"}[outcome_label]
    if out not in F:
        st.info(f"{outcome_label} is not in the database.")
    else:
        factors = [f for f in ["channel", "type", "segment", "company", "location", "language", "audience"] if f in F]
        sel = ", ".join(f"{C[f]} AS {q(f)}" for f in factors)
        raw, t = run(f"WITH f AS ({FILTERED}) SELECT {sel}, {C[out]} AS y FROM f", params, expanding); timings.append(t)
        raw = raw.dropna()
        if len(raw) < 50:
            st.warning("Too few campaigns for analysis. Widen the filters.")
        else:
            y = raw["y"].astype(float).to_numpy()
            sst = ((y - y.mean()) ** 2).sum()
            res = []
            for f in factors:
                groups = [g["y"].astype(float).to_numpy() for _, g in raw.groupby(f) if len(g) > 1]
                if len(groups) < 2:
                    continue
                F_, p_ = stats.f_oneway(*groups)
                ssb = sum(len(g) * (g.mean() - y.mean()) ** 2 for g in groups)
                eta = ssb / sst if sst else 0
                means = raw.groupby(f)["y"].mean()
                res.append({"Attribute": LABELS[f], "Groups": len(groups), "F": round(F_, 2),
                            "p-value": f"{p_:.4f}", "η²": eta,
                            "Effect": "negligible" if eta < 0.01 else "small" if eta < 0.06 else "medium" if eta < 0.14 else "large",
                            "Best group": f"{means.idxmax()} ({means.max():.4f})",
                            "Worst group": f"{means.idxmin()} ({means.min():.4f})"})
            res = pd.DataFrame(res).sort_values("η²", ascending=False)
            st.dataframe(res, width="stretch", hide_index=True,
                         column_config={"η²": st.column_config.NumberColumn(format="%.5f")})

            X = pd.get_dummies(raw[factors].astype(str), drop_first=True).to_numpy(dtype=float)
            X = np.column_stack([np.ones(len(X)), X])
            beta, *_ = np.linalg.lstsq(X, y, rcond=None)
            r2 = 1 - ((y - X @ beta) ** 2).sum() / sst
            top = res.iloc[0]
            st.markdown(
                f"**Interpretation.** Across {len(raw):,} campaigns, all {len(factors)} attributes together explain "
                f"**{r2:.2%}** of the variation in {outcome_label.lower()} (linear model). The strongest single "
                f"attribute is {top['Attribute'].lower()} (η² = {top['η²']:.5f}, {top['Effect']} effect). "
                + ("No attribute has a meaningful effect: in this dataset, campaign attributes do not explain "
                   "performance, so the dashboard should not recommend reallocating budget on these grounds."
                   if res["η²"].max() < 0.01 else
                   "Attributes with at least a small effect are worth testing further before changing budgets.")
            )
            st.markdown("**Two-attribute view**")
            a, b = st.columns(2)
            f1 = a.selectbox("Rows", factors, format_func=LABELS.get, index=0)
            f2 = b.selectbox("Columns", factors, format_func=LABELS.get, index=min(2, len(factors) - 1))
            if f1 != f2:
                piv = raw.pivot_table(index=f1, columns=f2, values="y", aggfunc="mean")
                cnt = raw.groupby([f1, f2]).size().min()
                st.plotly_chart(px.imshow(piv, text_auto=".3f", aspect="auto", color_continuous_scale="Oranges",
                                          title=f"Mean {outcome_label.lower()} by {LABELS[f1].lower()} and {LABELS[f2].lower()}",
                                          labels={"color": outcome_label}), width="stretch")
                st.caption(f"Smallest cell has {cnt:,} campaigns. Spread between best and worst cell: "
                           f"{piv.max().max() - piv.min().min():.4f}.")
            else:
                st.info("Choose two different attributes.")

st.divider()
st.caption(f"Database time for this page: {sum(timings):.2f} s across {len(timings)} queries "
           f"(cached for 10 minutes). CADP dashboard v{APP_VERSION}. Source code: github.com/hemantashrestha/cadp-dashboard")
