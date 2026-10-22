Changelog

Versions follow MAJOR.MINOR.PATCH. The version number is also shown at the bottom of the dashboard.

[2.1.0] - 2026-10-22
Changes made after the supervisor meeting of 21 October 2026. The dashboard is for senior managers, so it must be simple, look good, tell a story and end with recommendations.
Added
Executive Summary view, now the page that opens first. It tells the story from top to bottom in seven steps: how are we doing, where did the money go, what did we get back, is it getting better or worse, what drives results, can we trust these numbers, and what should we do.
KPI tiles that compare the latest month, quarter or half-year with the one before. Green means better, red means worse, and grey means a change under 1% (steady). For spend and cost per conversion, lower counts as better. Each tile has a small monthly trend line.
Each section heading states the finding in plain words (for example "Every channel costs about the same per conversion"), worked out from the live data, so the headings change with the filters.
Recommendations at the end of the page, generated from the figures above.
A fixed colour for each channel, the same in every chart. Green and red are kept for better and worse. Bars carry text labels so colour is never the only cue.
New chart types: donut (spend share), lollipop (ROI), trend lines with a "normal variation" band, and an effect-size bar chart with a 1% threshold line.
pipeline/data_rules.py: 15 rules for bad and missing data (fix, keep and flag, or reject with a reason). Missing conversion rates and costs are never filled in with a guess.
tests/test_bad_data.py: one deliberately broken row per rule; 27 of 27 checks pass.
Data Quality view lists the 15 rules and explains how bad and missing data are handled.
.streamlit/config.toml: blue theme colour, so red only ever means "worse".
Changed
The Executive Summary takes its channel, campaign type, monthly and ANOVA figures from one GROUPING SETS query, so the page needs two database queries (local test: 4.5 s from a cold start, 3.6 s of it database time).
Channel Performance charts use the channel colours.
The ANOVA code is shared by the Executive Summary and Diagnostic Analysis views.
requirements.txt needs Streamlit 1.66 or later (tile trend lines and arrow options).

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
