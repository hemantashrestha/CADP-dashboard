"""CADP bad and missing data rules (FR2, FR3).

The Kaggle file had no missing values and no duplicates, so these rules are
proven on deliberately broken test rows (tests/test_bad_data.py). Each rule
either FIXES a value, KEEPS the row with the value left empty (and flags it),
or REJECTS the row into a reject log with a reason. Nothing is ever guessed:
a missing conversion rate or cost is never filled with an average.

Usage:
    from pipeline.data_rules import clean_and_validate
    clean, rejects, flags = clean_and_validate(raw_df)
"""
import pandas as pd

# One row per rule, also shown on the dashboard's Data Quality view.
RULES = [
    ("R1", "Campaign_ID missing", "Reject row", "A campaign without an ID cannot be traced or reconciled."),
    ("R2", "Campaign_ID repeated", "Keep first, reject repeats", "Stops a campaign being counted twice."),
    ("R3", "Acquisition_Cost stored as text (\"$12,000.00\")", "Fix: strip $ and commas, convert to number", "Found in the real data; all 200,000 rows fixed."),
    ("R4", "Acquisition_Cost missing, unreadable, zero or negative", "Reject row", "Spend and cost per conversion would be wrong."),
    ("R5", "Duration stored as text (\"30 days\")", "Fix: keep the number of days", "Found in the real data; all rows fixed."),
    ("R6", "Duration missing or unreadable", "Keep row, leave empty, flag", "Duration is not used in any KPI."),
    ("R7", "Date stored as text", "Fix: convert to a date", "Found in the real data; all rows fixed."),
    ("R8", "Date missing or unreadable", "Reject row", "The row cannot be placed in a time period."),
    ("R9", "Clicks or Impressions missing or negative", "Reject row", "Core volume measures; never estimated."),
    ("R10", "Clicks greater than Impressions", "Reject row", "Impossible: a click needs an impression."),
    ("R11", "Conversion_Rate missing or outside 0 to 1", "Reject row", "Conversions are clicks x rate; a guessed rate would invent results."),
    ("R12", "ROI missing or not a number", "Keep row, leave empty, flag", "Row still counts for spend and clicks; left out of ROI averages."),
    ("R13", "Engagement_Score missing or outside 1 to 10", "Keep row, leave empty, flag", "Not a headline KPI."),
    ("R14", "Channel, campaign type, segment, company, location or language missing", "Keep row as \"Unknown\", flag", "Row is not lost in the star-schema joins and stays visible."),
    ("R15", "Extra spaces or wrong capitals in text (\" email \")", "Fix: trim and use the standard spelling", "Stops one channel splitting into two groups."),
]

CATEGORICAL = ["Company", "Campaign_Type", "Target_Audience", "Channel_Used", "Location",
               "Language", "Customer_Segment"]


def _num(s):
    return pd.to_numeric(s.astype(str).str.replace(r"[$,\s]", "", regex=True)
                         .replace({"": None, "nan": None, "None": None}), errors="coerce")


def clean_and_validate(raw: pd.DataFrame):
    """Returns (clean, rejects, flags). rejects and flags list Campaign_ID, rule and reason."""
    df = raw.copy().reset_index(drop=True)
    df["_row"] = df.index + 2  # line number in the CSV (header is line 1)
    rejects, flags = [], []

    def reject(mask, rule, reason):
        nonlocal df
        for _, r in df[mask].iterrows():
            rejects.append({"CSV line": r["_row"], "Campaign_ID": r.get("Campaign_ID"), "Rule": rule, "Reason": reason})
        df = df[~mask].copy()

    def flag(mask, rule, reason):
        for _, r in df[mask].iterrows():
            flags.append({"CSV line": r["_row"], "Campaign_ID": r.get("Campaign_ID"), "Rule": rule, "Reason": reason})

    # R15 and R14: tidy text, then standard spelling, then Unknown for blanks
    for c in [c for c in CATEGORICAL if c in df]:
        s = df[c].astype("string").str.strip().str.replace(r"\s+", " ", regex=True)
        canon = {}
        for v in s.dropna().value_counts().index:  # most common spelling wins
            canon.setdefault(v.lower(), v)
        s = s.map(lambda v: canon.get(v.lower(), v) if isinstance(v, str) else v)
        blank = s.isna() | (s == "")
        flag(blank, "R14", f"{c} missing, set to Unknown")
        df[c] = s.mask(blank, "Unknown")

    # R1, R2 Campaign_ID
    df["Campaign_ID"] = _num(df["Campaign_ID"])
    reject(df["Campaign_ID"].isna(), "R1", "Campaign_ID missing")
    reject(df["Campaign_ID"].duplicated(keep="first"), "R2", "Campaign_ID repeated")
    df["Campaign_ID"] = df["Campaign_ID"].astype("int64")

    # R3, R4 cost
    df["Acquisition_Cost"] = _num(df["Acquisition_Cost"])
    reject(df["Acquisition_Cost"].isna(), "R4", "Acquisition_Cost missing or unreadable")
    reject(df["Acquisition_Cost"] <= 0, "R4", "Acquisition_Cost zero or negative")

    # R5, R6 duration
    if "Duration" in df:
        d = pd.to_numeric(df["Duration"].astype(str).str.extract(r"(\d+)")[0], errors="coerce")
        flag(d.isna(), "R6", "Duration missing or unreadable, left empty")
        df["Duration"] = d.astype("Int64")

    # R7, R8 date
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    reject(df["Date"].isna(), "R8", "Date missing or unreadable")

    # R9, R10 clicks and impressions
    for c in ["Clicks", "Impressions"]:
        df[c] = _num(df[c])
        reject(df[c].isna() | (df[c] < 0), "R9", f"{c} missing or negative")
    reject(df["Clicks"] > df["Impressions"], "R10", "Clicks greater than impressions")
    df[["Clicks", "Impressions"]] = df[["Clicks", "Impressions"]].astype("int64")

    # R11 conversion rate
    df["Conversion_Rate"] = _num(df["Conversion_Rate"])
    reject(df["Conversion_Rate"].isna() | (df["Conversion_Rate"] < 0) | (df["Conversion_Rate"] > 1),
           "R11", "Conversion_Rate missing or outside 0 to 1")

    # R12 ROI, R13 engagement: keep, leave empty, flag
    df["ROI"] = _num(df["ROI"])
    flag(df["ROI"].isna(), "R12", "ROI missing, left empty")
    if "Engagement_Score" in df:
        e = _num(df["Engagement_Score"])
        bad = e.isna() | (e < 1) | (e > 10)
        flag(bad, "R13", "Engagement_Score missing or outside 1 to 10, left empty")
        df["Engagement_Score"] = e.mask(bad).astype("Int64")

    cols = ["CSV line", "Campaign_ID", "Rule", "Reason"]
    flags = pd.DataFrame(flags, columns=cols)
    flags = flags[flags["CSV line"].isin(df["_row"])]  # a rejected row is reported once, as a reject
    return (df.drop(columns="_row").reset_index(drop=True),
            pd.DataFrame(rejects, columns=cols), flags.reset_index(drop=True))
