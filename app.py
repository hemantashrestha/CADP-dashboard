
import streamlit as st
import pandas as pd
from sqlalchemy import create_engine
import plotly.express as px

st.set_page_config(page_title="Campaign Analytics Dashboard", layout="wide")

engine = create_engine(st.secrets["DB_CONNECTION"])

st.title("Campaign Analytics Dashboard")

channel_options = ["All"] + pd.read_sql(
    'SELECT DISTINCT "Channel_Used" FROM dim_channel ORDER BY "Channel_Used"', engine
)["Channel_Used"].tolist()

channel = st.selectbox("Filter by Channel", channel_options)

where_clause = "" if channel == "All" else f'WHERE c."Channel_Used" = \'{channel}\''

query = f"""
SELECT c."Channel_Used", SUM(f."Clicks") as total_clicks, AVG(f."Conversion_Rate") as avg_conversion
FROM fact_campaign_performance f
JOIN dim_channel c ON f.channel_id = c.channel_id
{where_clause}
GROUP BY c."Channel_Used"
"""
data = pd.read_sql(query, engine)

col1, col2 = st.columns(2)
with col1:
    st.plotly_chart(px.bar(data, x="Channel_Used", y="total_clicks", title="Clicks by Channel"), use_container_width=True)
with col2:
    st.plotly_chart(px.bar(data, x="Channel_Used", y="avg_conversion", title="Conversion Rate by Channel"), use_container_width=True)

st.subheader("Record-level drill-down")
if channel != "All":
    drill_query = f"""
    SELECT f."Campaign_ID", f."Company", f."Conversion_Rate", f."ROI", f."Acquisition_Cost"
    FROM fact_campaign_performance f
    JOIN dim_channel c ON f.channel_id = c.channel_id
    WHERE c."Channel_Used" = \'{channel}\'
    LIMIT 10
    """
    st.dataframe(pd.read_sql(drill_query, engine))
else:
    st.write("Select a specific channel above to see record-level data.")
