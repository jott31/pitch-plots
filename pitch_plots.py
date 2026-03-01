import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from pybaseball import statcast_pitcher, playerid_lookup, pitching_stats, schedule_and_record

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
    try:
        stats = pitching_stats(season, qual=0)
        if stats is None or stats.empty:
            return pd.DataFrame()
        return stats
    except Exception:
        return pd.DataFrame()

@st.cache_data
def get_season_dates(season, team_abbrev):
    schedule = schedule_and_record(season, team_abbrev)

    schedule = schedule.copy()
    schedule["Date"] = schedule["Date"].str.replace(r"\s*\(\d+\)$", "", regex=True)
    schedule["Date"] = pd.to_datetime(
        schedule["Date"].str.extract(r"(\w+,\s+\w+\s+\d+)")[0] + f" {season}",
        format="%A, %b %d %Y",
        errors="coerce"
    )

    schedule = schedule.dropna(subset=["Date"])
    start = schedule["Date"].min().strftime("%Y-%m-%d")
    end = schedule["Date"].max().strftime("%Y-%m-%d")

    return start, end
# ----------------------------
# App Title
# ----------------------------
st.title("Pitch Movement & Season Dashboard")

# ----------------------------
# Player Input
# ----------------------------
player_name = st.text_input("Enter Player Name", value="Hunter Greene")

if player_name:
    parts = player_name.strip().split(" ", 1)
    if len(parts) != 2:
        st.error("Enter a valid first and last name.")
        st.stop()

    first_name, last_name = parts

    try:
        player_info = playerid_lookup(last_name, first_name)
    except Exception as e:
        st.error(f"Error looking up player: {e}")
        st.stop()

    if player_info.empty:
        st.error("No players found.")
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
# Season / Year Selection
# ----------------------------
current_year = pd.Timestamp.now().year
available_years = list(range(2015, current_year + 1))

season = st.selectbox("Select Season", options=available_years[::-1], index=0)

fg_stats = get_season_stats(season)

if not fg_stats.empty:
    player_row = fg_stats[fg_stats["Name"].str.lower() == selected_player_name.lower()]
else:
    player_row = pd.DataFrame()

if not player_row.empty:
    team_abbrev = player_row["Team"].values[0]
    
    st.write(f"DEBUG — FanGraphs team abbrev: `{team_abbrev}`")  # temporary, remove later

    fg_to_pybaseball = {
        "ARI": "AZ",
        "ATL": "ATL",
        "BAL": "BAL",
        "BOS": "BOS",
        "CHC": "CHC",
        "CWS": "CWS",
        "CIN": "CIN",
        "CLE": "CLE",
        "COL": "COL",
        "DET": "DET",
        "HOU": "HOU",
        "KCR": "KC",
        "LAA": "LAA",
        "LAD": "LAD",
        "MIA": "MIA",
        "MIL": "MIL",
        "MIN": "MIN",
        "NYM": "NYM",
        "NYY": "NYY",
        "OAK": "OAK",
        "PHI": "PHI",
        "PIT": "PIT",
        "SDP": "SD",
        "SEA": "SEA",
        "SFG": "SF",
        "STL": "STL",
        "TBR": "TB",
        "TEX": "TEX",
        "TOR": "TOR",
        "WSN": "WSH",
    }

    team_abbrev = fg_to_pybaseball.get(team_abbrev, team_abbrev)
else:
    team_abbrev = "STL"
    st.warning("Could not load FanGraphs data — using default team schedule and season summary will be unavailable.")

# Extract team from Statcast data instead of FanGraphs
# Pull a small sample first just to get the team
with st.spinner("Fetching season schedule..."):
    try:
        sample_data = get_filtered_data(
            start_date=f"{season}-03-20",
            end_date=f"{season}-09-30",
            playerid=playerid
        )
        if not sample_data.empty:
            # Player's team is whichever team they appear for most
            team_abbrev = sample_data["home_team"].mode()[0]
        else:
            team_abbrev = "STL"
    except Exception:
        team_abbrev = "STL"

    try:
        start_date, end_date = get_season_dates(season, team_abbrev)
    except Exception as e:
        st.warning(f"Could not fetch schedule ({e}). Falling back to default dates.")
        start_date = f"{season}-03-20"
        end_date = f"{season}-09-30"
# ----------------------------
# Fetch Statcast Data
# ----------------------------
with st.spinner("Fetching Statcast data..."):
    data = get_filtered_data(
        start_date=start_date,
        end_date=end_date,
        playerid=playerid
    )

if data.empty:
    st.warning("No data available for the selected player and season.")
    st.stop()

st.success(f"Data loaded for {season} regular season.")

# ----------------------------
# Convert Movement to Inches
# ----------------------------
data["pfx_x"] = data["pfx_x"] * 12
data["pfx_z"] = data["pfx_z"] * 12

# ----------------------------
# Pitch Type Filter
# ----------------------------
pitch_types = sorted(data["pitch_type"].dropna().unique())

selected_pitches = st.multiselect(
    "Filter by Pitch Type",
    options=pitch_types,
    default=pitch_types
)

filtered_data = data[data["pitch_type"].isin(selected_pitches)]

# ----------------------------
# Advanced Pitch Metrics
# ----------------------------
swing_events = [
    "swinging_strike",
    "swinging_strike_blocked",
    "foul",
    "foul_tip",
    "hit_into_play"
]

whiff_events = [
    "swinging_strike",
    "swinging_strike_blocked"
]

metrics_df = (
    filtered_data
    .assign(
        is_swing=filtered_data["description"].isin(swing_events),
        is_whiff=filtered_data["description"].isin(whiff_events),
        in_zone=filtered_data["zone"].between(1, 9)
    )
    .groupby("pitch_type")
    .agg(
        Pitches=("pitch_type", "count"),
        Whiffs=("is_whiff", "sum"),
        Swings=("is_swing", "sum"),
        InZone=("in_zone", "sum")
    )
    .reset_index()
)

metrics_df["Whiff%"] = (
    metrics_df["Whiffs"] / metrics_df["Swings"]
).fillna(0) * 100

metrics_df["InZone%"] = (
    metrics_df["InZone"] / metrics_df["Pitches"]
) * 100

metrics_df = metrics_df[[
    "pitch_type",
    "Pitches",
    "Whiff%",
    "InZone%"
]]

# ----------------------------
# Season Summary (FanGraphs)
# ----------------------------
st.write("## Season Summary")

if not fg_stats.empty and not player_row.empty:
    summary_df = pd.DataFrame({...})
    st.dataframe(summary_df, use_container_width=True)
else:
    st.warning("Season summary unavailable — FanGraphs data could not be loaded for this season.")

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
# Pitch Type Metrics
# ----------------------------
st.write("## Pitch Type Metrics")

st.dataframe(
    metrics_df,
    column_config={
        "Whiff%": st.column_config.NumberColumn(format="%.1f%%"),
        "InZone%": st.column_config.NumberColumn(format="%.1f%%")
    },
    use_container_width=True
)

# ----------------------------
# Pitch Movement Plot
# ----------------------------
scatter_plot = px.scatter(
    filtered_data,
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
    filtered_data
    .groupby("pitch_type")[["release_pos_x", "release_pos_z"]]
    .mean()
    .reset_index()
)

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
scatter_plot_2 = px.scatter(
    filtered_data,
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

