Changelog

Versions follow MAJOR.MINOR.PATCH. The version number is also shown at the bottom of the dashboard.

[2.0.2] - 2026-10-15
Changed
Channel Performance table: plain-English column headings, formatted numbers (commas, $, %) and seven columns instead of ten. This responds to the Assessment 3 feedback on crowded tables and labels.
Conversion Funnel labels read "1.10 billion" and "110.0 million" instead of "1.10146B".
[2.0.1] - 2026-10-15
Changed
Faster first load (NFR1). The live v2.0.0 took 15.8 s of database time on first load. In local testing, v2.0.1 needs 0.7 to 2.1 s per view, down from 4.1 s for the whole v2.0.0 page.
A view selector replaces the tabs, so only the view on screen queries the database.
Diagnostic Analysis computes the ANOVA from per-group counts, means and variances returned by one GROUPING SETS query. It no longer downloads 200,000 rows. Results are identical to v2.0.0.
Data Quality runs one combined query instead of seven.
Filter lists read the small dimension tables instead of the full join.
The time-period filter is only applied when the range is narrowed.
Cost and ROI: the random 3,000-row scatter plot is replaced by average ROI across ten cost bands, plus a correlation calculated over all campaigns.
The conversion rate axis shows two decimal places (8.01% instead of 8.0%).
Removed
The all-attributes linear model in Diagnostic Analysis, because it needed all 200,000 rows in the app. The per-attribute ANOVA, which also reports p-values, covers FR10.
[2.0.0] - 2026-10-15
Added
Four more views: Campaign Overview, Cost and ROI, Conversion Funnel and Data Quality (FR6). All five planned views are now live.
Diagnostic Analysis tab: one-way ANOVA and eta squared for each campaign attribute, a linear model across all attributes, and a two-attribute heatmap (FR10).
Sidebar filters for time period, company, location, customer segment, campaign type, channel, language and target audience. Every filter applies to every view (FR7).
Drill-down to campaign records on every view, with totals that match the chart (FR8).
Live data-quality checks and reconciliation totals (FR2, FR9).
tests/cadp_tests.py for checks P1, P2, P6, P8, P9 and P10.
sql/readonly_role.sql for the read-only dashboard user (NFR6).
README and this changelog.
Changed
Queries use bound parameters instead of text inserted into SQL strings (NFR6).
Query results are cached for 10 minutes, and the page shows total database time (NFR1, check P6).
requirements.txt adds numpy and scipy and sets a minimum Streamlit version.
Moved
The version 1 dashboard is now archive/app_v1.py.
[1.0.0] - 2026-09-12
Added
First deployed dashboard: clicks and conversion rate by channel, a channel filter, and a ten-row record drill-down.
Connection to the Supabase PostgreSQL star schema through Streamlit secrets.
