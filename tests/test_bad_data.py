"""Proves the bad and missing data rules (pipeline/data_rules.py) on broken test rows.

The real Kaggle file has no missing values or duplicates, so we add one broken
row per rule to a small clean sample and check each one is caught correctly.
No database is needed.

Run:  python tests/test_bad_data.py      (or: pytest tests/test_bad_data.py)
Writes bad_data_test_results.csv for the final report.
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pipeline.data_rules import clean_and_validate  # noqa: E402


def good(i, **over):
    """A valid row in the same raw format as the Kaggle CSV."""
    row = {"Campaign_ID": i, "Company": "TechCorp", "Campaign_Type": "Email", "Target_Audience": "Men 18-24",
           "Duration": "30 days", "Channel_Used": "Email", "Conversion_Rate": 0.08,
           "Acquisition_Cost": "$12,500.00", "ROI": 5.0, "Location": "Chicago", "Language": "English",
           "Clicks": 500, "Impressions": 5000, "Engagement_Score": 6, "Customer_Segment": "Foodies",
           "Date": "2021-03-15"}
    row.update(over)
    return row


# (row, expected outcome, rule)
CASES = [
    (good(101), "kept", None),
    (good(102, Acquisition_Cost="$8,000.00"), "kept", "R3"),
    (good(103, Duration="45 days"), "kept", "R5"),
    (good(None), "rejected", "R1"),
    (good(101, Company="Duplicate of 101"), "rejected", "R2"),
    (good(104, Acquisition_Cost=None), "rejected", "R4"),
    (good(105, Acquisition_Cost="$-50.00"), "rejected", "R4"),
    (good(106, Acquisition_Cost="twelve"), "rejected", "R4"),
    (good(107, Duration=None), "flagged", "R6"),
    (good(108, Date="not a date"), "rejected", "R8"),
    (good(109, Date=None), "rejected", "R8"),
    (good(110, Clicks=None), "rejected", "R9"),
    (good(111, Impressions=-10), "rejected", "R9"),
    (good(112, Clicks=6000, Impressions=5000), "rejected", "R10"),
    (good(113, Conversion_Rate=None), "rejected", "R11"),
    (good(114, Conversion_Rate=1.7), "rejected", "R11"),
    (good(115, ROI=None), "flagged", "R12"),
    (good(116, Engagement_Score=14), "flagged", "R13"),
    (good(117, Channel_Used=None), "flagged", "R14"),
    (good(118, Customer_Segment=""), "flagged", "R14"),
    (good(119, Channel_Used="  email "), "kept", "R15"),
]


def run():
    raw = pd.DataFrame([c[0] for c in CASES])
    clean, rejects, flags = clean_and_validate(raw)
    out = []
    for line, (row, expected, rule) in enumerate(CASES, start=2):
        rj = rejects[rejects["CSV line"] == line]
        fl = flags[flags["CSV line"] == line]
        actual = "rejected" if len(rj) else "flagged" if len(fl) else "kept"
        got_rule = (rj["Rule"].iloc[0] if len(rj) else fl["Rule"].iloc[0] if len(fl) else rule)
        ok = actual == expected and got_rule == rule
        out.append({"CSV line": line, "Problem injected": rule or "none (clean row)", "Expected": expected,
                    "Actual": actual + (f" ({got_rule})" if got_rule else ""), "Result": "Pass" if ok else "Fail"})
    res = pd.DataFrame(out)

    # Values the fixes must produce
    c = clean.set_index("Campaign_ID")
    extra = [
        ("R3 cost text converted", c.loc[102, "Acquisition_Cost"] == 8000.0),
        ("R5 duration text converted", c.loc[103, "Duration"] == 45),
        ("R12 missing ROI left empty, not filled", pd.isna(c.loc[115, "ROI"])),
        ("R14 missing channel becomes Unknown", c.loc[117, "Channel_Used"] == "Unknown"),
        ("R15 '  email ' becomes 'Email'", c.loc[119, "Channel_Used"] == "Email"),
        ("Kept + rejected = rows in", len(clean) + len(rejects) == len(raw)),
    ]
    for name, ok in extra:
        res.loc[len(res)] = {"CSV line": "-", "Problem injected": name, "Expected": "True",
                             "Actual": str(bool(ok)), "Result": "Pass" if ok else "Fail"}
    return res, clean, rejects, flags


def test_bad_data_rules():
    res, *_ = run()
    assert (res["Result"] == "Pass").all(), res[res["Result"] != "Pass"].to_string()


if __name__ == "__main__":
    res, clean, rejects, flags = run()
    pd.set_option("display.width", 200)
    print(res.to_string(index=False))
    print(f"\nRows in: {len(clean) + len(rejects)} | kept: {len(clean)} | rejected: {len(rejects)} | "
          f"kept but flagged: {flags['CSV line'].nunique()}")
    print(f"Passed {(res['Result'] == 'Pass').sum()} of {len(res)} checks")
    res.to_csv("bad_data_test_results.csv", index=False)
