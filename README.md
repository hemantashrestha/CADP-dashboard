# CADP: Campaign Analytics Dashboard and Data Pipeline

ITSU3008 Project 1, VIT. Group Amir_ITSU3008_21.
Supervisor: Amir Masoud Rahmani.

CADP turns a synthetic marketing dataset (Kaggle, 200,000 campaigns, 16 fields) into a PostgreSQL star schema and a live Streamlit dashboard for account and agency managers.

- **Live dashboard:** https://cadp-dashboard-mcf3e999isscxkbkesiw8p.streamlit.app/
- **Current version:** 2.0.2 (see [CHANGELOG.md](CHANGELOG.md))

## What the dashboard shows

| View | Purpose | Requirement |
|---|---|---|
| Campaign Overview | Headline KPIs, monthly trends, spend by company | FR6 |
| Channel Performance | Clicks, conversion rate and ROI by channel | FR6 |
| Cost and ROI | Spend and cost per conversion by campaign type; ROI by cost band and correlation | FR6 |
| Conversion Funnel | Impressions to clicks to estimated conversions; conversions by segment | FR6 |
| Data Quality | Live checks on the database and reconciliation totals | FR2, FR9 |
| Diagnostic Analysis | ANOVA and effect size for each attribute; two-attribute heatmap | FR10 |

The sidebar filters (time period, company, location, customer segment, campaign type, channel, language, target audience) apply to every view (FR7). Every view has a drill-down to campaign records (FR8). Choose a view with the selector at the top of the page; only the selected view queries the database.

## Architecture

```
Kaggle CSV --> ETL in Colab (pandas) --> Supabase PostgreSQL (star schema) --> Streamlit app (this repo) --> browser
```

Star schema: `fact_campaign_performance` joined to `dim_channel`, `dim_campaign_type`, `dim_segment` and `dim_date`.

## Repository layout

| Path | Contents |
|---|---|
| `app.py` | The dashboard. Streamlit Cloud runs this file, so do not rename it. |
| `requirements.txt` | Python packages for Streamlit Cloud |
| `tests/cadp_tests.py` | Checks P1, P2, P6, P8, P9 and P10 against the live database (run in Colab) |
| `sql/readonly_role.sql` | Creates the read-only database user for the dashboard (NFR6) |
| `archive/app_v1.py` | Version 1 of the dashboard, kept for reference |

## Running it

**On Streamlit Cloud:** set a secret named `DB_CONNECTION` (App, then Settings, then Secrets):

```
DB_CONNECTION = "postgresql+psycopg://USER:PASSWORD@HOST:5432/postgres"
```

**Locally:**

```
pip install -r requirements.txt
# put the same line in .streamlit/secrets.toml
streamlit run app.py
```

**Tests:** see the instructions at the top of `tests/cadp_tests.py`.

## Security

- No passwords in this repository. The connection string lives only in Streamlit secrets.
- All filter values are sent to the database as bound parameters. Table and column names come from the database catalogue, never from user input.
- The dashboard is designed to connect as `cadp_reader`, a user that can only read the five CADP tables.

## Data and limitations

- All data is synthetic. There is no personal or real client information.
- The dataset has no customer IDs or touchpoint times, so attribution is campaign-level: each campaign's estimated conversions (clicks × conversion rate) are credited to its single channel.

## Team

| Member | Role |
|---|---|
| Hemanta Shrestha | Project Manager and Quality Manager |
| Sahil Kunwar | Data/ETL Manager and QA/Test Manager |
| Aashik Khadka | Analytics/Modelling Manager |
| Nikesh Maharjan | Dashboard/BI Manager |
