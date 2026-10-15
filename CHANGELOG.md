# Changelog

Versions follow MAJOR.MINOR.PATCH. The version number is also shown at the bottom of the dashboard.

## [2.0.0] - 2026-10-15

### Added
- Four more views: Campaign Overview, Cost and ROI, Conversion Funnel and Data Quality (FR6). All five planned views are now live.
- Diagnostic Analysis tab: one-way ANOVA and eta squared for each campaign attribute, a linear model across all attributes, and a two-attribute heatmap (FR10).
- Sidebar filters for time period, company, location, customer segment, campaign type, channel, language and target audience. Every filter applies to every view (FR7).
- Drill-down to campaign records on every view, with totals that match the chart (FR8).
- Live data-quality checks and reconciliation totals (FR2, FR9).
- `tests/cadp_tests.py` for checks P1, P2, P6, P8, P9 and P10.
- `sql/readonly_role.sql` for the read-only dashboard user (NFR6).
- README and this changelog.

### Changed
- Queries use bound parameters instead of text inserted into SQL strings (NFR6).
- Query results are cached for 10 minutes, and the page shows total database time (NFR1, check P6).
- `requirements.txt` adds numpy and scipy and sets a minimum Streamlit version.

### Moved
- The version 1 dashboard is now `archive/app_v1.py`.

## [1.0.0] - 2026-09-12

### Added
- First deployed dashboard: clicks and conversion rate by channel, a channel filter, and a ten-row record drill-down.
- Connection to the Supabase PostgreSQL star schema through Streamlit secrets.
