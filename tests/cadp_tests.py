"""CADP planned checks P1, P2, P6, P8, P9 and P10, run against the live database.

Run in Google Colab:
  1. Upload this file and marketing_campaign_dataset.csv (the original Kaggle file).
  2. In a cell:  !pip -q install "psycopg[binary]" sqlalchemy pandas
  3. Edit the three settings below, then in a cell:  !python cadp_tests.py
Results are printed and saved to cadp_test_results.csv for the final report.
"""
import hashlib
import statistics
import time

import pandas as pd
from sqlalchemy import create_engine, text

# ------------------------------------------------------------------ settings
CSV_PATH = "marketing_campaign_dataset.csv"
OWNER_URL = "PASTE the same connection string as the Streamlit secret DB_CONNECTION"
READER_URL = ""   # connection string for cadp_reader (leave empty to skip P9)
# ---------------------------------------------------------------------------

FACT = "fact_campaign_performance"


def engine(url):
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return create_engine(url, pool_pre_ping=True)


def clean(raw):
    """Same transform as the team's ETL (FR3)."""
    df = raw.copy()
    df["Acquisition_Cost"] = df["Acquisition_Cost"].astype(str).str.replace(r"[$,]", "", regex=True).astype(float)
    df["Duration"] = df["Duration"].astype(str).str.extract(r"(\d+)")[0].astype(int)
    df["Date"] = pd.to_datetime(df["Date"])
    return df


def within(a, b, tol=0.0001):
    return abs(a - b) <= tol * max(abs(b), 1)


results = []


def record(pid, check, expected, actual, passed):
    results.append({"ID": pid, "Check": check, "Expected": expected, "Actual": actual,
                    "Result": "Pass" if passed else "Fail"})
    print(f"[{'PASS' if passed else 'FAIL'}] {pid} {check}: {actual}")


eng = engine(OWNER_URL)
raw = pd.read_csv(CSV_PATH)
src = clean(raw)

with eng.connect() as c:
    cols = [r[0] for r in c.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_name = :t"), {"t": FACT})]

    # P1: one row per campaign
    n, distinct = c.execute(text(f'SELECT COUNT(*), COUNT(DISTINCT "Campaign_ID") FROM {FACT}')).one()
    record("P1", "Campaign_ID is unique", "No repeated IDs", f"{n:,} rows, {distinct:,} distinct IDs", n == distinct)

    # P2: totals reconciliation, database vs source CSV, within 0.01 per cent
    db = c.execute(text(f'SELECT COUNT(*), SUM("Clicks"), SUM("Impressions"), SUM("Acquisition_Cost") FROM {FACT}')).one()
    db_rows, db_clicks, db_impr, db_cost = int(db[0]), float(db[1]), float(db[2]), float(db[3])
    pairs = [("rows", db_rows, len(src)), ("clicks", db_clicks, src["Clicks"].sum()),
             ("impressions", db_impr, src["Impressions"].sum()), ("spend", db_cost, src["Acquisition_Cost"].sum())]
    ok = all(within(d, s) for _, d, s in pairs)
    record("P2", "Totals reconciliation (database vs CSV)", "Within 0.01 per cent",
           "; ".join(f"{k} DB {d:,.0f} / CSV {s:,.0f}" for k, d, s in pairs), ok)

    # P6: response time of the main dashboard query (5 runs, median)
    q = text(f'SELECT d."Channel_Used", COUNT(*), SUM(f."Clicks"), AVG(f."Conversion_Rate"), AVG(f."ROI") '
             f'FROM {FACT} f JOIN dim_channel d ON f.channel_id = d.channel_id GROUP BY 1')
    times = []
    for _ in range(5):
        t0 = time.perf_counter(); c.execute(q).fetchall(); times.append(time.perf_counter() - t0)
    med = statistics.median(times)
    record("P6", "Response time, channel query (median of 5)", "3 s or less (filter update)", f"{med:.2f} s", med <= 3)

    # P10: is Conversion_Rate per click or per impression?
    imp_basis = (src["Impressions"] * src["Conversion_Rate"] > src["Clicks"]).mean()
    record("P10", "Conversion_Rate basis (data check)", "Per click if per-impression would exceed clicks",
           f"{imp_basis:.1%} of rows would have more conversions than clicks if the rate were per impression",
           imp_basis > 0.05)

# P8: ETL repeatability: run the transform twice and compare
h1 = hashlib.sha256(pd.util.hash_pandas_object(clean(raw), index=True).values).hexdigest()
h2 = hashlib.sha256(pd.util.hash_pandas_object(clean(raw), index=True).values).hexdigest()
record("P8", "ETL repeatability (two runs, same file)", "Identical output", f"hash match: {h1 == h2} ({h1[:12]})", h1 == h2)

# P9: read-only role cannot write
if READER_URL:
    rdr = engine(READER_URL)
    with rdr.connect() as c:
        can_read = c.execute(text(f"SELECT COUNT(*) FROM {FACT}")).scalar() > 0
        try:
            c.execute(text(f'UPDATE {FACT} SET "Clicks" = "Clicks" WHERE false'))
            c.execute(text("CREATE TABLE cadp_write_test (x int)"))
            blocked = False
        except Exception as e:
            blocked = "permission denied" in str(e).lower()
        c.rollback()
    record("P9", "Read-only role rejects writes", "Reads allowed, writes rejected",
           f"read ok: {can_read}; write blocked: {blocked}", can_read and blocked)
else:
    print("[SKIP] P9: READER_URL not set")

out = pd.DataFrame(results)
out.to_csv("cadp_test_results.csv", index=False)
print("\n", out[["ID", "Check", "Result"]].to_string(index=False))
