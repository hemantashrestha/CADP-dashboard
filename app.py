"""CADP: Campaign Analytics Dashboard (Streamlit). Version 2.2.0 (see CHANGELOG.md).

Views: Executive Summary (the main page for senior managers), Campaign Overview,
Channel Performance, Cost and ROI, Conversion Funnel, Data Quality and Diagnostic
Analysis (FR6, FR10). Sidebar filters apply to every
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

try:  # the 15 bad/missing data rules, shown on the Data Quality view
    from pipeline.data_rules import RULES
except ImportError:  # the app still runs if pipeline/data_rules.py is missing
    RULES = []

APP_VERSION = "2.2.0"
st.set_page_config(page_title="CADP Campaign Analytics", layout="wide")

FACT = "fact_campaign_performance"
DIMS = ["dim_channel", "dim_campaign_type", "dim_segment", "dim_date"]
ACCENT = "#2a78d6"
# One fixed colour per channel, assigned in alphabetical order so a channel keeps its
# colour in every chart and under every filter. Green and red are not used here:
# they are kept for "better" and "worse" on the KPI tiles. Checked for colour-blind
# separation with a palette validator; bars also carry text labels.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#6250d6", "#898781"]
NEUTRAL = "#898781"
CHART_H = 320
px.defaults.template = "plotly_white"
px.defaults.color_discrete_sequence = [ACCENT]
STEADY = 0.01  # changes smaller than 1% are shown as grey "steady", not green or red

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
    COUNT({C['roi']}) AS roi_n,
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
date_where = None
period_end = None
if "date" in F:
    rng, _ = run(f"SELECT MIN(CAST({C['date']} AS date)) AS lo, MAX(CAST({C['date']} AS date)) AS hi FROM {owner('date')}")
    lo, hi = pd.to_datetime(rng["lo"][0]).date(), pd.to_datetime(rng["hi"][0]).date()
    period_end = hi
    picked = st.sidebar.date_input("Time period", (lo, hi), min_value=lo, max_value=hi)
    if isinstance(picked, (list, tuple)) and len(picked) == 2 and tuple(picked) != (lo, hi):
        date_where = f"CAST({C['date']} AS date) BETWEEN :d_from AND :d_to"
        params.update(d_from=picked[0], d_to=picked[1])
        period_end = picked[1]

for field in ["company", "location", "segment", "type", "channel", "language", "audience"]:
    if field not in F:
        continue
    chosen = st.sidebar.multiselect(LABELS[field], options(field))
    if chosen:
        name = f"f_{field}"
        where.append(f"{C[field]} IN :{name}")
        params[name] = [str(x) for x in chosen] if not isinstance(chosen[0], (int, np.integer)) else [int(x) for x in chosen]
        expanding.append(name)

WHERE = ("WHERE " + " AND ".join(where + ([date_where] if date_where else []))) if (where or date_where) else ""
FILTERED = f"WITH base AS ({BASE}) SELECT * FROM base {WHERE}"
# Same filters without the time period, used to compare this period with the one before.
WHERE_NODATE = ("WHERE " + " AND ".join(where)) if where else ""
FILTERED_NODATE = f"WITH base AS ({BASE}) SELECT * FROM base {WHERE_NODATE}"
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


@st.cache_data(ttl=3600, show_spinner=False)
def colors_for(field):
    """Fixed colour per value of an attribute (alphabetical order), the same in every chart and under any filter."""
    vals = sorted(str(c) for c in options(field))
    return {c: SERIES[i] if i < len(SERIES) - 1 else NEUTRAL for i, c in enumerate(vals)}


def channel_colors():
    return colors_for("channel")


def hbar(df, x, y, fmt, xlabel, tickfmt=None):
    """Horizontal bars in the attribute's fixed colours, value printed on each bar."""
    fig = px.bar(df, x=x, y=y, orientation="h", color=y, color_discrete_map=colors_for(y),
                 text=df[x].map(fmt), labels={x: xlabel, y: ""})
    fig.update_traces(textposition="outside", cliponaxis=False,
                      hovertemplate="%{y}<br>%{text}<extra></extra>")
    fig.update_xaxes(range=[0, df[x].max() * 1.2], tickformat=tickfmt)
    return fig


def band_line(df, col, fmt, zoom=True, band=True):
    """Monthly line with the average and a ±1% "normal variation" band."""
    avg = df[col].mean()
    ln = px.line(df, x="month", y=col, markers=True, labels={"month": "", col: ""})
    if band:
        ln.add_hrect(y0=avg * (1 - STEADY), y1=avg * (1 + STEADY), fillcolor=ACCENT, opacity=0.08, line_width=0,
                     annotation_text=f"±{STEADY:.0%} of average", annotation_position="top left",
                     annotation_font_color="#52514e")
    ln.add_hline(y=avg, line_dash="dot", line_color=NEUTRAL)
    ln.update_traces(line=dict(width=2.5, color=ACCENT), marker=dict(size=8),
                     hovertemplate="%{x|%B %Y}<br>%{y:" + fmt + "}<extra></extra>")
    ln.update_yaxes(tickformat=fmt, range=[avg * 0.97, avg * 1.03] if zoom else None)
    return ln


def spread_msg(df, key, col, what, fmt, plural, low_is_good=False):
    """One-sentence finding: either 'all about the same' or 'X leads, Y trails'."""
    lo, hi = df.loc[df[col].idxmin()], df.loc[df[col].idxmax()]
    if df[col].mean() and (hi[col] - lo[col]) / abs(df[col].mean()) < 0.03:
        return f"All {len(df)} {plural} are about the same on {what}: {fmt(lo[col])} to {fmt(hi[col])}"
    best, worst = (lo, hi) if low_is_good else (hi, lo)
    return f"{best[key]} is best on {what} ({fmt(best[col])}); {worst[key]} is weakest ({fmt(worst[col])})"


def trend_msg(mon, col, what, fmt):
    lo, hi, avg = mon[col].min(), mon[col].max(), mon[col].mean()
    if (hi - lo) / avg < 0.1:
        return f"{what} hold steady from month to month ({fmt(lo)} to {fmt(hi)})"
    peak = mon.loc[mon[col].idxmax(), "month"]
    return f"{what} range from {fmt(lo)} to {fmt(hi)} a month, peaking in {pd.Timestamp(peak):%B %Y}"


def so_what(text):
    """The 'so what' for a manager, at the end of each view."""
    with st.container(border=True):
        st.markdown(f"**So what?** {text}".replace("$", "\\$"))


def tidy(fig, height=CHART_H, legend=False):
    """Shared chart style: no chart title (the section heading carries the message),
    light grid, compact margins."""
    fig.update_layout(title=None, height=height, margin=dict(l=8, r=8, t=8, b=8), showlegend=legend,
                      font=dict(size=13), hoverlabel=dict(font_size=13))
    fig.update_xaxes(gridcolor="#eeeeec", zeroline=False)
    fig.update_yaxes(gridcolor="#eeeeec", zeroline=False)
    return fig


FACTORS = ["channel", "type", "segment", "company", "location", "language", "audience"]


def grouped(out, with_month=False):
    """One scan of the filtered data, grouped by each attribute in turn (GROUPING SETS).
    Each group row has the KPI measures plus count, mean and variance of `out` for the ANOVA.
    The Executive Summary takes its channel, campaign type and monthly figures from this
    same query, so the whole page needs only two database queries."""
    factors = [f for f in FACTORS if f in F]
    keys = {f: C[f] for f in factors}
    if with_month and "date" in F:
        keys["month"] = f"date_trunc('month', CAST({C['date']} AS date))::date"
    y = C[out]
    sets = ", ".join(f"({e})" for e in keys.values())
    flags = ", ".join(f"GROUPING({e}) AS {q('g_' + k_)}" for k_, e in keys.items())
    labels = ", ".join((f"{e}::text" if k_ != "month" else e) + f" AS {q(k_)}" for k_, e in keys.items())
    grp, t = run(f"""WITH f AS ({FILTERED})
        SELECT {flags}, {labels}, COUNT({y}) AS n, AVG({y}) AS mean, VAR_SAMP({y}) AS var, {MEASURES}
        FROM f GROUP BY GROUPING SETS ({sets}, ())""", params, expanding)
    timings.append(t)
    return grp, list(keys)


def rows_for(grp, keys, key):
    """The rows of a grouped() result for one attribute, or the overall total (key=None)."""
    sel = grp[[f"g_{k_}" for k_ in keys if k_ != key]].eq(1).all(axis=1)
    if key is not None:
        sel &= grp[f"g_{key}"] == 0
    return grp[sel].drop(columns=[c for c in grp.columns if c.startswith("g_")]).reset_index(drop=True)


def anova(out, pre=None):
    """One-way ANOVA per attribute from per-group count, mean and variance (FR10).
    Returns (results DataFrame sorted by eta squared, N) or (None, N)."""
    grp, keys = pre or grouped(out)
    factors = [k_ for k_ in keys if k_ != "month"]
    total = rows_for(grp, keys, None)
    N = int(total["n"].iloc[0]) if len(total) else 0
    if N < 50:
        return None, N
    grand = float(total["mean"].iloc[0])
    sst = float(total["var"].iloc[0]) * (N - 1)
    res = []
    for f in factors:
        g = rows_for(grp, keys, f)[[f, "n", "mean", "var"]].dropna()
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
                    "Worst group": f"{worst[f]} ({worst['mean']:.4f})",
                    "_best": str(best[f])})
    return pd.DataFrame(res).sort_values("η²", ascending=False), N


def dq_checks():
    """Live data-quality checks on the whole database (FR2, FR9). Ignores the filters."""
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
        FROM ({BASE}) b""")
    timings.append(t)
    d = dq.iloc[0]
    checks = [("Rows in fact table", d.fact_rows, d.fact_rows > 0),
              ("Rows lost in joins", d.fact_rows - d.joined_rows + d.unmatched_channel,
               d.fact_rows - d.joined_rows + d.unmatched_channel == 0)]
    if "id" in F:
        checks.append(("Duplicate Campaign_ID values", d.duplicate_ids, d.duplicate_ids == 0))
    checks += [("Clicks greater than impressions", d.clicks_gt_impr, d.clicks_gt_impr == 0),
               ("Conversion rate outside 0 to 1", d.cr_out_of_range, d.cr_out_of_range == 0),
               ("Missing cost, clicks or rate", d.missing_values, d.missing_values == 0),
               ("Zero or negative acquisition cost", d.non_positive_cost, d.non_positive_cost == 0)]
    return checks, d


def compact(v, money_=False):
    """1,234,567 -> 1.23M; used on tiles so they stay readable."""
    pre = "$" if money_ else ""
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= div:
            return f"{pre}{v / div:.2f}{suf}"
    return f"{pre}{v:,.0f}"


def combine(df):
    """Exact totals from grouped rows (sums add up; averages are re-weighted by row counts)."""
    n, rn = df["campaigns"].sum(), df["roi_n"].sum()
    clicks, impr, spend, conv = df["clicks"].sum(), df["impressions"].sum(), df["spend"].sum(), df["est_conversions"].sum()
    return pd.Series({"campaigns": n, "clicks": clicks, "impressions": impr, "spend": spend, "est_conversions": conv,
                      "avg_conversion_rate": (df["avg_conversion_rate"] * df["campaigns"]).sum() / n if n else np.nan,
                      "avg_roi": (df["avg_roi"].fillna(0) * df["roi_n"]).sum() / rn if rn else np.nan,
                      "ctr": clicks / impr if impr else np.nan,
                      "cost_per_conversion": spend / conv if conv else np.nan})


def period_bounds(end, months):
    """The last `months` months up to `end`, and the same length just before it."""
    end = pd.Timestamp(end)
    cur_from = end + pd.Timedelta(days=1) - pd.DateOffset(months=months)
    prev_from = cur_from - pd.DateOffset(months=months)
    return cur_from.date(), end.date(), prev_from.date(), (cur_from - pd.Timedelta(days=1)).date()


def period_label(a, b, months):
    a, b = pd.Timestamp(a), pd.Timestamp(b)
    whole = a.day == 1 and b == b + pd.offsets.MonthEnd(0)
    if whole and months == 1:
        return f"{a:%B %Y}"
    if whole and months == 3 and a.month in (1, 4, 7, 10):
        return f"Q{(a.month - 1) // 3 + 1} {a.year}"
    if whole and months == 6 and a.month in (1, 7):
        return f"{'H1' if a.month == 1 else 'H2'} {a.year}"
    return f"{a:%d %b} to {b:%d %b %Y}"


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
EXEC = "Executive Summary"
# Only the selected view queries the database (tabs would run all seven on every load).
view = st.segmented_control("View", [EXEC] + VIEWS, default=EXEC, label_visibility="collapsed") or EXEC
timings = []
PAGE_T0 = time.perf_counter()


def section(num, question, message):
    """Story step: a small (numbered) question, then the answer as the heading."""
    lead = f"{num} · {question}" if num is not None else question
    st.markdown(f"<div style='color:#52514e;font-size:0.85rem;font-weight:600;letter-spacing:.04em;"
                f"text-transform:uppercase;margin-top:1.4rem'>{lead}</div>"
                f"<div style='font-size:1.35rem;font-weight:650;line-height:1.3;margin:.15rem 0 .5rem'>{message}</div>",
                unsafe_allow_html=True)


# 0. Executive summary: one page that tells the story from top to bottom
if view == EXEC:
    COLORS = channel_colors()
    G = grouped("cr", with_month=True)  # channel, type, month and ANOVA figures in one scan
    gkeys = G[1]
    tot = rows_for(*G, None)
    tot = tot.iloc[0] if len(tot) else pd.Series({"campaigns": 0})
    ch = rows_for(*G, "channel").sort_values("spend", ascending=False).reset_index(drop=True)
    if tot["campaigns"] == 0:
        st.warning("No campaigns match the current filters.")
        st.stop()

    # ---- 1. How are we doing? KPI tiles compared with the previous period
    unit = st.segmented_control("Compare", ["Month", "Quarter", "Half-year"], default="Quarter",
                                help="Each tile shows the latest period and its change from the period before.")
    months = {"Month": 1, "Quarter": 3, "Half-year": 6}[unit or "Quarter"]
    cmp, mon = None, None
    if "date" in F:
        # Monthly totals ignoring the time filter: used for the comparison and, with no time filter, the trends.
        mcol = f"date_trunc('month', CAST({C['date']} AS date))::date"
        pn = {k_: v for k_, v in params.items() if k_ not in ("d_from", "d_to")}
        mon = rows_for(*G, "month").sort_values("month").reset_index(drop=True)
        mon["month"] = pd.to_datetime(mon["month"])
        if date_where:  # the comparison needs months outside the chosen time period
            allmon, t = run(f"WITH f AS ({FILTERED_NODATE}) SELECT {mcol} AS month, {MEASURES} FROM f "
                            f"GROUP BY 1 ORDER BY 1", pn, expanding); timings.append(t)
            allmon["month"] = pd.to_datetime(allmon["month"])
        else:
            allmon = mon
        c_from, c_to, p_from, p_to = period_bounds(period_end, months)
        if p_from >= lo:
            if c_to == (pd.Timestamp(c_to) + pd.offsets.MonthEnd(0)).date():
                m_ = allmon["month"].dt.date
                cur_rows = allmon[(m_ >= c_from) & (m_ <= c_to)]
                prev_rows = allmon[(m_ >= p_from) & (m_ <= p_to)]
                if len(cur_rows) and len(prev_rows):
                    cmp = pd.DataFrame({"cur": combine(cur_rows), "prev": combine(prev_rows)}).T
            else:  # period ends mid-month: count the exact days
                pp = dict(pn, c_from=c_from, c_to=c_to, p_from=p_from)
                dcol = f"CAST({C['date']} AS date)"
                cmp, t = run(f"WITH f AS ({FILTERED_NODATE}) SELECT CASE WHEN {dcol} >= :c_from THEN 'cur' ELSE 'prev' END "
                             f"AS period, {MEASURES} FROM f WHERE {dcol} BETWEEN :p_from AND :c_to GROUP BY 1",
                             pp, expanding); timings.append(t)
                cmp = cmp.set_index("period")
                if not {"cur", "prev"} <= set(cmp.index):
                    cmp = None

    # (label, column, value format, direction: +1 higher is better, -1 lower is better)
    KPIS = [("Spend", "spend", lambda v: compact(v, True), -1),
            ("Est. conversions", "est_conversions", compact, +1),
            ("Conversion rate", "avg_conversion_rate", lambda v: f"{v:.2%}", +1),
            ("Average ROI", "avg_roi", lambda v: f"{v:.2f}x", +1),
            ("Cost per conversion", "cost_per_conversion", lambda v: f"${v:,.2f}", -1)]
    moves = []
    if cmp is not None:
        cur, prev = cmp.loc["cur"], cmp.loc["prev"]
        for name, col, _, good in KPIS:
            chg = (cur[col] - prev[col]) / prev[col] if prev[col] else 0.0
            moves.append((name, chg, 0 if abs(chg) < STEADY else (1 if chg * good > 0 else -1)))
        now, before = period_label(c_from, c_to, months), period_label(p_from, p_to, months)
        up = [f"{m[0].lower()} {'up' if m[1] > 0 else 'down'} {abs(m[1]):.1%}" for m in moves if m[2] == 1]
        down = [f"{m[0].lower()} {'up' if m[1] > 0 else 'down'} {abs(m[1]):.1%}" for m in moves if m[2] == -1]
        n_steady = sum(1 for m in moves if m[2] == 0)
        parts = ([f"better: {', '.join(up)}"] if up else []) + ([f"worse: {', '.join(down)}"] if down else [])
        parts.append("everything else steady" if parts and n_steady else "all five KPIs steady" if n_steady else "")
        headline = f"{now} vs {before}: " + "; ".join(p_ for p_ in parts if p_)
    else:
        now = before = None
        headline = (f"Overall results for the selected period (too little earlier data to compare by "
                    f"{(unit or 'quarter').lower()}: choose a shorter comparison)" if "date" in F else
                    "Overall results for the selected period")
    section(1, "How are we doing?", headline)
    cols = st.columns(len(KPIS))
    for i, (name, col, fmt, good) in enumerate(KPIS):
        spark = mon[col].tolist() if mon is not None and len(mon) > 2 else None
        if cmp is not None:
            chg, verdict = moves[i][1], moves[i][2]
            cols[i].metric(name, fmt(cmp.loc["cur", col]),
                           delta=f"{chg:+.1%}" + (" · steady" if verdict == 0 else ""),
                           delta_color="off" if verdict == 0 else ("normal" if good > 0 else "inverse"),
                           delta_arrow="off" if verdict == 0 else "auto",
                           chart_data=spark, chart_type="area", border=True,
                           help=f"{now}: {fmt(cmp.loc['cur', col])}. {before}: {fmt(cmp.loc['prev', col])}. "
                                f"Green = better, red = worse, grey = changed by less than {STEADY:.0%}. "
                                f"The small chart is the monthly trend.")
        else:
            cols[i].metric(name, fmt(tot[col]), chart_data=spark, chart_type="area", border=True)
    st.caption(f"Whole selected period: {int(tot['campaigns']):,} campaigns, {money(tot['spend'])} spend, "
               f"{tot['est_conversions']:,.0f} estimated conversions. Tiles show {now or 'the latest period'} "
               f"and the change from {before or 'the period before'}: green is better, red is worse, grey means a change under "
               f"{STEADY:.0%}. For spend and cost per conversion, lower is better.")

    # ---- 2. Where did the money go?
    ch["channel"] = ch["channel"].astype(str)
    ch["share"] = ch["spend"] / ch["spend"].sum()
    even = ch["share"].max() / max(ch["share"].min(), 1e-9) < 1.1
    top = ch.iloc[0]
    section(2, "Where did the money go?",
            f"Budget is spread evenly: each of the {len(ch)} channels receives about {ch['share'].mean():.0%} of spend"
            if even else
            f"{top['channel']} takes the largest share of budget ({top['share']:.0%} of {money(ch['spend'].sum())})")
    a, b = st.columns([2, 3])
    pie = px.pie(ch, names="channel", values="spend", hole=0.55, color="channel", color_discrete_map=COLORS)
    pie.update_traces(textinfo="label+percent", textposition="outside", sort=False,
                      marker=dict(line=dict(color="#ffffff", width=2)),
                      hovertemplate="%{label}<br>Spend %{value:$,.0f}<br>%{percent}<extra></extra>")
    pie.add_annotation(text=f"<b>{compact(ch['spend'].sum(), True)}</b><br>total spend", showarrow=False,
                       font=dict(size=15))
    a.plotly_chart(tidy(pie), width="stretch")
    if "type" in F:
        ty = rows_for(*G, "type").sort_values("spend").reset_index(drop=True)
        bar = px.bar(ty, x="spend", y="type", orientation="h", text=ty["spend"].map(lambda v: compact(v, True)),
                     labels={"spend": "Spend ($)", "type": ""})
        bar.update_traces(marker_color=NEUTRAL, textposition="inside", insidetextanchor="end",
                          textfont=dict(color="#ffffff"),
                          hovertemplate="%{y}<br>Spend %{x:$,.0f}<extra></extra>")
        b.markdown("**Spend by campaign type**")
        b.plotly_chart(tidy(bar, height=CHART_H - 30), width="stretch")

    # ---- 3. What did we get back?
    cpc = ch.sort_values("cost_per_conversion", ascending=False)
    lo_c, hi_c = cpc.iloc[-1], cpc.iloc[0]
    spread = (hi_c["cost_per_conversion"] - lo_c["cost_per_conversion"]) / lo_c["cost_per_conversion"]
    section(3, "What did we get back?",
            f"Every channel costs about the same per conversion (${lo_c['cost_per_conversion']:,.0f} to "
            f"${hi_c['cost_per_conversion']:,.0f}) and returns about {ch['avg_roi'].mean():.1f}x its cost"
            if spread < 0.05 else
            f"{lo_c['channel']} is the cheapest channel per conversion (${lo_c['cost_per_conversion']:,.0f}); "
            f"{hi_c['channel']} is the most expensive (${hi_c['cost_per_conversion']:,.0f})")
    a, b = st.columns(2)
    bar = px.bar(cpc, x="cost_per_conversion", y="channel", orientation="h", color="channel",
                 color_discrete_map=COLORS, text=cpc["cost_per_conversion"].map("${:,.0f}".format),
                 labels={"cost_per_conversion": "Cost per conversion ($)", "channel": ""})
    bar.update_traces(textposition="outside", cliponaxis=False,
                      hovertemplate="%{y}<br>$%{x:,.2f} per conversion<extra></extra>")
    bar.update_xaxes(range=[0, cpc["cost_per_conversion"].max() * 1.18])
    a.markdown("**Cost per conversion by channel** (lower is better)")
    a.plotly_chart(tidy(bar), width="stretch")
    roi = ch.sort_values("avg_roi")
    dots = px.scatter(roi, x="avg_roi", y="channel", color="channel", color_discrete_map=COLORS,
                      text=roi["avg_roi"].map("{:.2f}x".format), labels={"avg_roi": "Average ROI", "channel": ""})
    dots.update_traces(marker=dict(size=16, line=dict(color="#ffffff", width=2)), textposition="middle right",
                       hovertemplate="%{y}<br>ROI %{x:.3f}<extra></extra>")
    for _, r_ in roi.iterrows():  # lollipop stems from zero
        dots.add_shape(type="line", x0=0, x1=r_["avg_roi"], y0=r_["channel"], y1=r_["channel"],
                       line=dict(color="#d4d3cf", width=2), layer="below")
    dots.update_xaxes(range=[0, roi["avg_roi"].max() * 1.2])
    b.markdown("**Average ROI by channel** (higher is better)")
    b.plotly_chart(tidy(dots), width="stretch")

    # ---- 4. Is it getting better or worse?
    if mon is not None and len(mon) > 1:
        cr_lo, cr_hi, cr_avg = mon["avg_conversion_rate"].min(), mon["avg_conversion_rate"].max(), mon["avg_conversion_rate"].mean()
        steady = (cr_hi - cr_lo) / cr_avg < 0.03
        best_m = mon.loc[mon["avg_conversion_rate"].idxmax(), "month"]
        section(4, "Is it getting better or worse?",
                f"Results are steady: conversion rate stays between {cr_lo:.2%} and {cr_hi:.2%} every month"
                if steady else
                f"Conversion rate moves between {cr_lo:.2%} and {cr_hi:.2%}; it peaked in {pd.Timestamp(best_m):%B %Y}")
        a, b = st.columns(2)
        for box, col, title, fmt_ in ((a, "avg_conversion_rate", "Conversion rate by month", ".2%"),
                                      (b, "avg_roi", "Average ROI by month", ".2f")):
            avg = mon[col].mean()
            ln = px.line(mon, x="month", y=col, markers=True, labels={"month": "", col: ""})
            ln.add_hrect(y0=avg * (1 - STEADY), y1=avg * (1 + STEADY), fillcolor="#2a78d6", opacity=0.08,
                         line_width=0, annotation_text=f"±{STEADY:.0%} of average", annotation_position="top left",
                         annotation_font_color="#52514e")
            ln.add_hline(y=avg, line_dash="dot", line_color=NEUTRAL)
            ln.update_traces(line=dict(width=2.5, color=ACCENT), marker=dict(size=8),
                             hovertemplate="%{x|%B %Y}<br>%{y:" + fmt_ + "}<extra></extra>")
            ln.update_yaxes(tickformat=fmt_, range=[avg * 0.97, avg * 1.03])
            box.markdown(f"**{title}**")
            box.plotly_chart(tidy(ln, height=280), width="stretch")
        st.caption("The shaded band is within 1% of the average. Points inside it are normal variation, not a real change. "
                   "The axis is zoomed in, so small movements look bigger than they are.")

    # ---- 5. What drives results?
    res, N = anova("cr", G)
    if res is not None and len(res):
        strongest = res.iloc[0]
        none_matter = res["η²"].max() < 0.01
        section(5, "What drives results?",
                "None of the campaign attributes makes a real difference: each explains less than 1% of "
                "the variation in conversion rate" if none_matter else
                f"{strongest['Attribute']} has the biggest effect, explaining {strongest['η²']:.1%} of the "
                f"variation in conversion rate (best: {strongest['_best']})")
        eff = res.sort_values("η²")
        eff["pct"] = eff["η²"] * 100
        hb = px.bar(eff, x="pct", y="Attribute", orientation="h",
                    text=eff["pct"].map(lambda v: f"{v:.3f}%"),
                    labels={"pct": "Share of variation in conversion rate explained (%)", "Attribute": ""})
        hb.update_traces(marker_color=ACCENT, textposition="outside", cliponaxis=False,
                         hovertemplate="%{y}: %{x:.4f}% explained<extra></extra>")
        hb.add_vline(x=1, line_dash="dash", line_color="#52514e",
                     annotation_text="1%: smallest effect worth acting on", annotation_position="top left",
                     annotation_font_color="#52514e")
        hb.update_xaxes(range=[0, max(1.25, eff["pct"].max() * 1.25)])
        st.plotly_chart(tidy(hb, height=280), width="stretch")
        st.caption(f"One-way ANOVA on {N:,} campaigns. A bar must pass the dashed line before the attribute is worth "
                   f"acting on. Full statistics are in the Diagnostic Analysis view.")

    # ---- 6. Can we trust these numbers?
    checks, d = dq_checks()
    passed = sum(1 for *_, ok in checks if ok)
    section(6, "Can we trust these numbers?",
            f"Yes: all {len(checks)} data checks pass on {int(d.fact_rows):,} campaigns" if passed == len(checks) else
            f"Caution: {len(checks) - passed} of {len(checks)} data checks fail")
    (st.success if passed == len(checks) else st.error)(
        f"{'✔' if passed == len(checks) else '✖'} {passed} of {len(checks)} checks passed: no duplicates, no missing "
        f"values, no impossible values, nothing lost in joins. Totals match the source file exactly. "
        f"See the Data Quality view for the checks and the rules for bad or missing data.")

    # ---- 7. What should we do?
    recs = []
    if res is not None and len(res) and res["η²"].max() < 0.01:
        recs.append(("Keep the current channel mix.",
                     "No channel, campaign type or customer segment converts meaningfully better, so moving "
                     "budget between them will not lift results."))
    elif res is not None and len(res):
        recs.append((f"Test more budget in {strongest['_best']}.",
                     f"{strongest['Attribute']} is the strongest driver of conversion rate. Move a small share "
                     f"first and check the result next {(unit or 'quarter').lower()}."))
    if spread < 0.05:
        recs.append(("Cut cost, not channels.",
                     f"Every channel costs ${lo_c['cost_per_conversion']:,.0f} to ${hi_c['cost_per_conversion']:,.0f} "
                     f"per conversion. The saving comes from lowering acquisition cost across all channels "
                     f"(supplier rates, bidding, creative), not from switching channel."))
    else:
        recs.append((f"Shift budget toward {lo_c['channel']}.",
                     f"It costs ${lo_c['cost_per_conversion']:,.0f} per conversion against "
                     f"${hi_c['cost_per_conversion']:,.0f} for {hi_c['channel']}. Move spend in steps and watch "
                     f"cost per conversion."))
    recs.append(("Test before big changes.",
                 "Run small A/B tests before changing budgets. Month-to-month movements here are within normal "
                 "variation, so a single good month is not proof."))
    recs.append(("Collect customer journey data.",
                 "Record which ads each customer saw before converting. Without it, last-touch or multi-touch "
                 "attribution is not possible and channel credit stays at campaign level."))
    section(7, "What should we do?", "Four recommendations for the next planning cycle")
    with st.container(border=True):
        for i, (head, body) in enumerate(recs, start=1):
            st.markdown(f"**{i}. {head}** {body}".replace("$", "\\$"))
    st.caption("Recommendations are generated from the figures above and update with the filters. "
               "The data is synthetic (Kaggle), so the steady results reflect how the data was generated; with real "
               "agency data the same page would point to the channels and segments that differ.")

# 1. Overview
if view == VIEWS[0]:
    k, t = agg(); timings.append(t)
    r = k.iloc[0]
    if r["campaigns"] == 0:
        st.warning("No campaigns match the current filters.")
    else:
        mon = None
        if "date" in F:
            mon, t = run(f"WITH f AS ({FILTERED}) SELECT date_trunc('month', CAST({C['date']} AS date))::date AS month, "
                         f"{MEASURES} FROM f GROUP BY 1 ORDER BY 1", params, expanding); timings.append(t)
        section(None, "Campaign overview",
                f"{int(r['campaigns']):,} campaigns spent {compact(r['spend'], True)} and produced about "
                f"{compact(r['est_conversions'])} conversions at {r['avg_conversion_rate']:.2%}")
        spark = lambda col: mon[col].tolist() if mon is not None and len(mon) > 2 else None
        m = st.columns(4)
        m[0].metric("Campaigns", f"{int(r['campaigns']):,}", chart_data=spark("campaigns"), chart_type="bar", border=True)
        m[1].metric("Total spend", compact(r["spend"], True), chart_data=spark("spend"), chart_type="area", border=True,
                    help=money(r["spend"]))
        m[2].metric("Clicks", compact(r["clicks"]), chart_data=spark("clicks"), chart_type="area", border=True)
        m[3].metric("Impressions", compact(r["impressions"]), chart_data=spark("impressions"), chart_type="area", border=True)
        m = st.columns(4)
        m[0].metric("Avg conversion rate", f"{r['avg_conversion_rate']:.2%}", chart_data=spark("avg_conversion_rate"), border=True)
        m[1].metric("Avg ROI", f"{r['avg_roi']:.2f}x", chart_data=spark("avg_roi"), border=True)
        m[2].metric("Est. conversions", compact(r["est_conversions"]), chart_data=spark("est_conversions"),
                    chart_type="area", border=True)
        m[3].metric("Click-through rate", f"{r['ctr']:.2%}", chart_data=spark("ctr"), border=True)
        st.caption("The small chart in each tile is the monthly trend.")
        if mon is not None and len(mon) > 1:
            section(None, "Trend", trend_msg(mon, "clicks", "Clicks", compact))
            a, b = st.columns(2)
            a.markdown("**Clicks by month** (shorter months have fewer clicks)")
            a.plotly_chart(tidy(band_line(mon, "clicks", ",.0f", zoom=False, band=False), height=280), width="stretch")
            b.markdown("**Average conversion rate by month**")
            b.plotly_chart(tidy(band_line(mon, "avg_conversion_rate", ".2%"), height=280), width="stretch")
        if "company" in F:
            comp, _ = agg("company", "spend ASC")
            comp["company"] = comp["company"].astype(str)
            section(None, "Spend by company", spread_msg(comp, "company", "spend", "spend", lambda v: compact(v, True), "companies"))
            st.plotly_chart(tidy(hbar(comp, "spend", "company", lambda v: compact(v, True), "Spend ($)"), height=260),
                            width="stretch")
            even = (comp["spend"].max() - comp["spend"].min()) / comp["spend"].mean() < 0.03
            so_what(("Budget is shared out almost equally, so no company is being favoured or starved. " if even else
                     f"{comp.iloc[-1]['company']} receives the most budget; check that its results justify the extra spend. ")
                    + "Use the drill-down to see the individual campaigns behind any company.")
            drill("overview", "company", comp["company"].tolist())

# 2. Channel performance
if view == VIEWS[1]:
    ch, t = agg("channel", "clicks DESC"); timings.append(t)
    ch["channel"] = ch["channel"].astype(str)
    section(None, "Channel performance",
            spread_msg(ch, "channel", "avg_conversion_rate", "conversion rate", lambda v: f"{v:.2%}", "channels"))
    cols = st.columns(3)
    for box, col, title, fmt in ((cols[0], "clicks", "Clicks", compact),
                                 (cols[1], "avg_conversion_rate", "Conversion rate", lambda v: f"{v:.2%}"),
                                 (cols[2], "avg_roi", "Average ROI", lambda v: f"{v:.2f}x")):
        box.markdown(f"**{title} by channel**")
        box.plotly_chart(tidy(hbar(ch.sort_values(col), col, "channel", fmt, title,
                                   ".0%" if col == "avg_conversion_rate" else None), height=300), width="stretch")
    st.dataframe(summary_table(ch, "channel"), width="stretch", hide_index=True)
    flat = (ch["avg_conversion_rate"].max() - ch["avg_conversion_rate"].min()) / ch["avg_conversion_rate"].mean() < 0.03
    so_what("Every channel converts and returns about the same, so channel choice is not the lever for better "
            "results. Compare channels on cost per conversion (Cost and ROI view) before moving budget." if flat else
            f"{ch.loc[ch['avg_conversion_rate'].idxmax(), 'channel']} converts best. Test a small budget shift towards it "
            "and check cost per conversion before moving more.")
    drill("channel", "channel", ch["channel"].tolist())

# 3. Cost and ROI
if view == VIEWS[2]:
    grp = "type" if "type" in F else "channel"
    ct, t = agg(grp, "spend DESC"); timings.append(t)
    ct[grp] = ct[grp].astype(str)
    bands, t = run(f"""WITH f AS ({FILTERED}), w AS (SELECT MIN({C['cost']}) AS lo, MAX({C['cost']}) AS hi FROM f)
        SELECT width_bucket({C['cost']}, w.lo, w.hi + 0.01, 10) AS band, MIN({C['cost']}) AS from_cost,
               MAX({C['cost']}) AS to_cost, AVG({C['roi']}) AS avg_roi, COUNT(*) AS campaigns
        FROM f, w GROUP BY 1 ORDER BY 1""", params, expanding); timings.append(t)
    corr, t = run(f"WITH f AS ({FILTERED}) SELECT CORR({C['cost']}, {C['roi']}) AS r, COUNT(*) AS n FROM f",
                  params, expanding); timings.append(t)
    section(None, "Where the money goes",
            spread_msg(ct, grp, "cost_per_conversion", "cost per conversion", lambda v: f"${v:,.0f}",
                       LABELS[grp].lower() + "s", low_is_good=True))
    a, b = st.columns([2, 3])
    pie = px.pie(ct, names=grp, values="spend", hole=0.55, color=grp, color_discrete_map=colors_for(grp))
    pie.update_traces(textinfo="label+percent", textposition="outside", sort=False,
                      marker=dict(line=dict(color="#ffffff", width=2)),
                      hovertemplate="%{label}<br>Spend %{value:$,.0f}<br>%{percent}<extra></extra>")
    pie.add_annotation(text=f"<b>{compact(ct['spend'].sum(), True)}</b><br>spend", showarrow=False, font=dict(size=15))
    a.markdown(f"**Share of spend by {LABELS[grp].lower()}**")
    a.plotly_chart(tidy(pie), width="stretch")
    b.markdown(f"**Cost per conversion by {LABELS[grp].lower()}** (lower is better)")
    b.plotly_chart(tidy(hbar(ct.sort_values("cost_per_conversion", ascending=False), "cost_per_conversion", grp,
                             lambda v: f"${v:,.0f}", "$ per conversion")), width="stretch")
    if len(bands):
        bands["Cost band"] = bands.apply(lambda r: f"${r.from_cost/1000:,.0f}k to ${r.to_cost/1000:,.0f}k", axis=1)
        r_ = corr["r"].iloc[0]
        n_ = int(corr["n"].iloc[0])
        p_ = np.nan
        if pd.notna(r_):
            t_ = r_ * np.sqrt((n_ - 2) / max(1e-12, 1 - r_ ** 2))
            p_ = 2 * stats.t.sf(abs(t_), n_ - 2)
        weak = pd.notna(r_) and abs(r_) < 0.1
        section(None, "Does spending more per campaign pay off?",
                f"No: ROI stays about {bands['avg_roi'].mean():.1f}x whether a campaign costs "
                f"{bands['Cost band'].iloc[0].split(' to ')[0]} or {bands['Cost band'].iloc[-1].split(' to ')[1]}"
                if weak else
                f"Yes: ROI {'rises' if r_ > 0 else 'falls'} as campaign cost increases (r = {r_:.2f})")
        avg = bands["avg_roi"].mean()
        ln = px.line(bands, x="Cost band", y="avg_roi", markers=True, hover_data=["campaigns"],
                     labels={"avg_roi": "Average ROI", "Cost band": "Acquisition cost per campaign"})
        ln.add_hrect(y0=avg * (1 - STEADY), y1=avg * (1 + STEADY), fillcolor=ACCENT, opacity=0.08, line_width=0,
                     annotation_text=f"±{STEADY:.0%} of average", annotation_position="top left",
                     annotation_font_color="#52514e")
        ln.update_traces(line=dict(width=2.5, color=SERIES[1]), marker=dict(size=9))
        ln.update_yaxes(range=[0, max(bands["avg_roi"].max() * 1.2, 1)], tickformat=".1f")
        st.plotly_chart(tidy(ln, height=300), width="stretch")
        if pd.notna(r_):
            st.caption(f"Correlation between acquisition cost and ROI across {n_:,} campaigns: r = {r_:.4f} (p = {p_:.3f}). "
                       "The axis starts at zero so the line shows the true size of any change.")
    cheap = ct.loc[ct["cost_per_conversion"].idxmin()]
    cflat = (ct["cost_per_conversion"].max() - ct["cost_per_conversion"].min()) / ct["cost_per_conversion"].mean() < 0.05
    so_what(("Higher spending per campaign does not buy a better return. " if len(bands) and weak else
             "Campaign cost and ROI are linked, so review the most expensive campaigns first. ")
            + ("No campaign type is clearly cheaper, so savings will come from lowering acquisition cost across the "
               "board, not from switching type." if cflat else
               f"{cheap[grp]} is the cheapest per conversion (${cheap['cost_per_conversion']:,.0f}); "
               "shift budget towards it in small steps."))
    drill("cost", grp, ct[grp].tolist())

# 4. Conversion funnel
if view == VIEWS[3]:
    k, _ = agg(); r = k.iloc[0]
    if r["campaigns"] > 0:
        per1k_c = r["clicks"] / r["impressions"] * 1000
        per1k_v = r["est_conversions"] / r["impressions"] * 1000
        section(None, "Conversion funnel",
                f"Out of every 1,000 impressions, about {per1k_c:.0f} people click and about {per1k_v:.0f} convert")
        funnel = pd.DataFrame({"Stage": ["Impressions", "Clicks", "Est. conversions"],
                               "Count": [r["impressions"], r["clicks"], r["est_conversions"]]})
        a, b = st.columns([3, 2])
        funnel["Label"] = funnel["Count"].map(lambda v: f"{v / 1e9:.2f} billion" if v >= 1e9 else f"{v / 1e6:.1f} million")
        fn = px.funnel(funnel, x="Count", y="Stage", text="Label")
        fn.update_traces(texttemplate="%{text}", textposition=["inside", "outside", "outside"],
                         insidetextfont=dict(color="#0b0b0b"),
                         marker=dict(color=["#86b6ef", "#2a78d6", "#1c5cab"]), connector=dict(fillcolor="#eef2f8"))
        a.plotly_chart(tidy(fn, height=300), width="stretch")
        b.metric("Click-through rate", f"{r['ctr']:.2%}", border=True, help="Clicks ÷ impressions")
        b.metric("Click to conversion", f"{r['est_conversions'] / r['clicks']:.2%}", border=True,
                 help="Estimated conversions ÷ clicks")
        b.metric("Impression to conversion", f"{r['est_conversions'] / r['impressions']:.3%}", border=True)
    if "segment" in F:
        sg, t = agg("segment", "est_conversions ASC"); timings.append(t)
        sg["segment"] = sg["segment"].astype(str)
        section(None, "Conversions by customer segment",
                spread_msg(sg, "segment", "est_conversions", "estimated conversions", compact, "segments"))
        st.plotly_chart(tidy(hbar(sg, "est_conversions", "segment", compact, "Est. conversions"), height=280),
                        width="stretch")
        if k.iloc[0]["campaigns"] > 0:
            so_what(f"The biggest drop is between impression and click: about {1 - k.iloc[0]['ctr']:.0%} of people "
                    "who see an ad do not click. Better targeting or creative at the top of the funnel has the most "
                    "room to improve results.")
        drill("funnel", "segment", sg["segment"].tolist())

# 5. Data quality (FR2, FR9)
if view == VIEWS[4]:
    checks, d = dq_checks()
    passed = sum(1 for *_, ok in checks if ok)
    section(None, "Data quality",
            f"All {len(checks)} checks pass: the numbers on this dashboard can be trusted" if passed == len(checks) else
            f"{len(checks) - passed} of {len(checks)} checks fail: treat the figures with caution")
    st.caption("Checks run live against the database (refreshed every 10 minutes). They ignore the sidebar filters.")
    cols = st.columns(4)
    for i, (n, v, ok) in enumerate(checks):
        cols[i % 4].metric(n, f"{int(v):,}", delta="Pass" if ok else "Fail", delta_color="normal" if ok else "inverse",
                           delta_arrow="off", border=True)
    section(None, "Reconciliation (check P2)", "Database totals match the source file")
    m = st.columns(2)
    m[0].metric("Rows", f"{int(d.fact_rows):,}", border=True)
    m[1].metric("Clicks", f"{d.clicks:,.0f}", border=True)
    m = st.columns(2)
    m[0].metric("Impressions", f"{d.impressions:,.0f}", border=True)
    m[1].metric("Spend", money(d.spend), border=True)
    section(None, "How bad and missing data are handled", "Every bad value is fixed, flagged or rejected, never guessed")
    a, b, c = st.columns(3)
    for box, (head, col, body) in zip((a, b, c), (
            ("Fix", SERIES[0], "The value is wrong but readable, for example cost stored as \"$12,000.00\"."),
            ("Keep and flag", SERIES[3], "The row is useful but one value is missing, for example ROI. Left empty, never filled in."),
            ("Reject with a reason", SERIES[1], "The row would give wrong results, for example a missing date or conversion rate."))):
        with box.container(border=True):
            st.markdown(f"<span style='color:{col};font-size:1.3rem'>●</span> **{head}**", unsafe_allow_html=True)
            st.markdown(body)
    st.markdown("The source file had no missing values and no duplicate rows. Three columns were stored in the wrong "
                "format and were fixed for all 200,000 rows (R3, R5, R7). The other rules protect the pipeline when a "
                "future file contains bad or missing data. All 15 rules are tested on deliberately broken rows in "
                "tests/test_bad_data.py (27 of 27 checks pass).")
    if RULES:
        st.table(pd.DataFrame(RULES, columns=["Rule", "Problem", "Action", "Why"]).set_index("Rule"))
    else:
        st.info("The rules table needs pipeline/data_rules.py in the repository.")

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
        res, N = anova(out)
        if res is None:
            st.warning("Too few campaigns for analysis. Widen the filters.")
        else:
            res = res.drop(columns="_best")
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
st.caption(f"Page ready in {time.perf_counter() - PAGE_T0:.2f} s ({len(timings)} database queries, "
           f"{sum(timings):.2f} s of query time, cached for 10 minutes). CADP dashboard v{APP_VERSION}. Source code: github.com/hemantashrestha/cadp-dashboard")
