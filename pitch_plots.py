import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from pybaseball import statcast_pitcher, playerid_lookup, pitching_stats

# ----------------------------
# Pitch Type Mapping
# ----------------------------
pitch_colors_mapping = {
    "FF": "#FF007D",
    "SI": "#98165D",
    "FC": "#BE5FA0",
    "CH": "#F79E70",
    "FS": "#FE6100",
    "SL": "#67E18D",
    "ST": "#1BB999",
    "SV": "#376748",
    "KC": "#311D8B",
    "CU": "#3025CE",
    "CS": "#274BFC",
    "KN": "#867A08",
    "EP": "#648FFF",
    "PO": "#472C30",
    "UN": "#9C8975"
}

# ----------------------------
# Cache Statcast Data
# ----------------------------
@st.cache_data
def get_filtered_data(start_date, end_date, playerid):
    return statcast_pitcher(start_dt=start_date, end_dt=end_date, player_id=playerid)

@st.cache_data
def get_season_stats(season):
    return pitching_stats(season, qual=0)

# ----------------------------
# App Title
# ----------------------------
st.title("Pitch Movement & Season Dashboard")

# ----------------------------
# Player Input
# ----------------------------

player_name = st.text_input("Enter Player Name", value="Hunter Greene")

if player_name:
    try:
        first_name, last_name = player_name.split(" ")
        player_info = playerid_lookup(last_name, first_name)
    except ValueError:
        st.error("Enter a valid first and last name")
        st.stop()

    if player_info.empty:
        st.error("No players found")
        st.stop()

    named_id_mapping = {
        f"{row['name_first']} {row['name_last']}": {
            "mlbam": row["key_mlbam"],
            "fangraphs": row["key_fangraphs"]
        }
        for _, row in player_info.iterrows()
    }

    selected_player_name = st.selectbox(
        "Select player",
        options=named_id_mapping.keys()
    )

    playerid = named_id_mapping[selected_player_name]["mlbam"]
    fangraphs_id = named_id_mapping[selected_player_name]["fangraphs"]

else:
    st.stop()

# ----------------------------
# Date Selection
# ----------------------------
start_date = st.date_input("Start Date", value=pd.to_datetime("2025-03-20"))
end_date = st.date_input("End Date", value=pd.to_datetime("2025-09-30"))

if start_date > end_date:
    st.error("Start date must be before or equal to end date.")
    st.stop()
    

# ----------------------------
# Fetch Data
# ----------------------------
with st.spinner("Fetching Statcast data..."):
    data = get_filtered_data(
        start_date=start_date.strftime("%Y-%m-%d"),
        end_date=end_date.strftime("%Y-%m-%d"),
        playerid=playerid
    )

if data.empty:
    st.warning("No data available for selected player/date range.")
    st.stop()

st.success(f"Data loaded from {start_date} to {end_date}")

# ----------------------------
# Convert Movement to Inches
# ----------------------------
data["pfx_x"] = data["pfx_x"] * 12
data["pfx_z"] = data["pfx_z"] * 12

# ----------------------------
# Season Summary (FanGraphs via IDfg)
# ----------------------------
st.write("## Season Summary")

season = start_date.year
fg_stats = get_season_stats(season)

player_row = fg_stats[
    fg_stats["Name"].str.lower() == selected_player_name.lower()
]

if not player_row.empty:

    summary_df = pd.DataFrame({
        "Season": [season],
        "IP": [round(player_row["IP"].values[0], 1)],
        "ERA": [round(player_row["ERA"].values[0], 2)],
        "FIP": [round(player_row["FIP"].values[0], 2)],
        "K%": [round(player_row["K%"].values[0] * 100, 1)],
        "BB%": [round(player_row["BB%"].values[0] * 100, 1)]
    })

    st.dataframe(summary_df, use_container_width=True)

else:
    st.warning("No matching season data found.")
# ----------------------------
# Pitch Movement Plot
# ----------------------------
st.write("## Pitch Movement (Horizontal vs Vertical Break)")

scatter_plot = px.scatter(
    data,
    x="pfx_x",
    y="pfx_z",
    color="pitch_type",
    title="Pitch Movement",
    labels={
        "pfx_x": "Horizontal Break (inches)",
        "pfx_z": "Vertical Break (inches)"
    },
    hover_data=["release_speed", "pitch_type"],
    color_discrete_map=pitch_colors_mapping
)

scatter_plot.update_xaxes(range=[25, -25])
scatter_plot.update_yaxes(range=[-25, 25])

# ----------------------------
# Compute Average Arm Slot per Pitch Type
# ----------------------------
release_df = (
    data
    .groupby("pitch_type")[["release_pos_x", "release_pos_z"]]
    .mean()
    .reset_index()
)

# Scale factor so lines are visible on movement plot
scale_factor = 5

# ----------------------------
# Add Arm Slot Lines
# ----------------------------

for _, row in release_df.iterrows():
    
    pitch = row["pitch_type"]
    x_val = row["release_pos_x"] * scale_factor
    y_val = row["release_pos_z"] * scale_factor
    
    scatter_plot.add_trace(
        go.Scatter(
            x=[0, x_val],
            y=[0, y_val],
            mode="lines",
            line=dict(
                dash="dash",
                width=3,
                color=pitch_colors_mapping.get(pitch, "black")
            ),
            name=f"{pitch} Arm Slot",
            showlegend=False
        )
    )

# ----------------------------
# Pitch Location Plot
# ----------------------------
st.write("## Pitch Location")

scatter_plot_2 = px.scatter(
    data,
    x="plate_x",
    y="plate_z",
    color="pitch_type",
    title="Pitch Location",
    labels={
        "plate_x": "Horizontal Location",
        "plate_z": "Vertical Location"
    },
    hover_data=["release_speed", "pitch_type"],
    color_discrete_map=pitch_colors_mapping
)



# Strike Zone Overlay
scatter_plot_2.add_shape(
    type="rect",
    x0=-0.83, x1=0.83,
    y0=1.5, y1=3.5,
    line=dict(width=2)
)

scatter_plot_2.update_xaxes(range=[2, -2])
scatter_plot_2.update_yaxes(range=[0, 6])

st.markdown("---")

col1, col2 = st.columns(2)

with col1:
    st.write("### Pitch Movement")
    st.plotly_chart(scatter_plot, use_container_width=True)

with col2:
    st.write("### Pitch Location")
    st.plotly_chart(scatter_plot_2, use_container_width=True)

