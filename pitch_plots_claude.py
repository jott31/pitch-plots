import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from pybaseball import statcast_pitcher, playerid_lookup, pitching_stats, schedule_and_record
from pybaseball.playerid_lookup import get_lookup_table

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

pitch_name_mapping = {
    "FF": "4-Seam Fastball",
    "SI": "Sinker",
    "FC": "Cutter",
    "CH": "Changeup",
    "FS": "Splitter",
    "SL": "Slider",
    "ST": "Sweeper",
    "SV": "Slurve",
    "KC": "Knuckle-Curve",
    "CU": "Curveball",
    "CS": "Slow Curve",
    "KN": "Knuckleball",
    "EP": "Eephus",
    "PO": "Pitch Out",
    "UN": "Unknown"
}

# ----------------------------
# Cached Data Functions
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
    """
    Fetch exact regular season start and end dates from the
    player's team schedule via Baseball Reference.
    """
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

@st.cache_data
def load_lookup_table():
    """
    Load the full Chadwick player register once and cache it.
    This powers all search queries locally without repeated API calls.
    """
    return get_lookup_table()


# ----------------------------
# App Title
# ----------------------------
st.title("Pitch Movement & Season Dashboard")

# ----------------------------
# Load Lookup Table Once
# ----------------------------
with st.spinner("Loading player database..."):
    lookup_table = load_lookup_table()

# ----------------------------
# FIND PITCHER — Single searchable selectbox (type to filter, click to select)
# ----------------------------
st.sidebar.header("🔍 Find Pitcher")

# Build the full player list once for the selectbox
named_id_mapping = {}
player_labels = []

valid_players = lookup_table[
    lookup_table["key_mlbam"].notna() & (lookup_table["key_mlbam"] != "")
].drop_duplicates(subset=["key_mlbam"]).copy()

for _, row in valid_players.iterrows():
    first = str(row["name_first"]).title()
    last = str(row["name_last"]).title()
    first_year = int(row["mlb_played_first"]) if pd.notna(row.get("mlb_played_first")) else None
    last_year = int(row["mlb_played_last"]) if pd.notna(row.get("mlb_played_last")) else None
    year_str = f" ({first_year}–{last_year})" if first_year else ""
    label = f"{first} {last}{year_str}"
    if label not in named_id_mapping:
        player_labels.append(label)
        named_id_mapping[label] = {
            "mlbam": row["key_mlbam"],
            "fangraphs": row["key_fangraphs"],
            "display_name": f"{first} {last}",
        }

# Single box: type a name to filter the dropdown, then select
selected_label = st.sidebar.selectbox(
    "Search & select pitcher",
    options=player_labels,
    index=None,
    placeholder="Type a name to search…",
    help="Start typing a first or last name — the list filters as you type"
)

if selected_label is None:
    st.sidebar.info("Type a name above to find a pitcher.")
    st.info("👈 Use the sidebar to search for a pitcher to get started.")
    st.stop()

playerid = named_id_mapping[selected_label]["mlbam"]
fangraphs_id = named_id_mapping[selected_label]["fangraphs"]
selected_player_name = named_id_mapping[selected_label]["display_name"]

# ----------------------------
# Season / Year Selection — only seasons with Statcast data for this pitcher
# ----------------------------
st.sidebar.markdown("---")
st.sidebar.header("📅 Season")

@st.cache_data
def get_available_seasons(playerid):
    """
    Scan each year from 2015 to present and return only seasons
    where this pitcher has Statcast pitch data.
    """
    current_year = pd.Timestamp.now().year
    available = []
    for yr in range(current_year, 2014, -1):
        try:
            sample = statcast_pitcher(
                start_dt=f"{yr}-03-20",
                end_dt=f"{yr}-11-01",
                player_id=playerid
            )
            if sample is not None and not sample.empty:
                available.append(yr)
        except Exception:
            continue
    return available

with st.sidebar:
    with st.spinner("Finding available seasons..."):
        available_years = get_available_seasons(playerid)

if not available_years:
    st.sidebar.error("No Statcast data found for this pitcher.")
    st.stop()

season = st.sidebar.selectbox("Select Season", options=available_years, index=0)

# ----------------------------
# Fetch Season Stats
# ----------------------------
fg_stats = get_season_stats(season)

if not fg_stats.empty:
    player_row = fg_stats[fg_stats["Name"].str.lower() == selected_player_name.lower()]
else:
    player_row = pd.DataFrame()

# ----------------------------
# Identify Team from Statcast Sample
# ----------------------------
with st.spinner("Identifying team schedule..."):
    try:
        sample = get_filtered_data(
            start_date=f"{season}-03-20",
            end_date=f"{season}-09-30",
            playerid=playerid
        )
        team_abbrev = sample["home_team"].mode()[0] if not sample.empty else None
    except Exception:
        team_abbrev = None

# ----------------------------
# Fetch Exact Season Dates
# ----------------------------
if team_abbrev:
    try:
        start_date, end_date = get_season_dates(season, team_abbrev)
    except Exception:
        start_date = f"{season}-03-20"
        end_date = f"{season}-09-30"
else:
    start_date = f"{season}-03-20"
    end_date = f"{season}-09-30"

st.caption(f"Regular season window: {start_date} → {end_date}")

# ----------------------------
# Fetch Full Statcast Data
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
        InZone=("in_zone", "sum"),
        AvgVelo=("release_speed", "mean"),
        MaxVelo=("release_speed", "max")
    )
    .reset_index()
)

metrics_df["Whiff%"] = (
    metrics_df["Whiffs"] / metrics_df["Swings"]
).fillna(0) * 100

metrics_df["InZone%"] = (
    metrics_df["InZone"] / metrics_df["Pitches"]
) * 100

metrics_df["Pitch%"] = (
    metrics_df["Pitches"] / metrics_df["Pitches"].sum()
) * 100

metrics_df = metrics_df[[
    "pitch_type",
    "Pitches",
    "Pitch%",
    "AvgVelo",
    "MaxVelo",
    "Whiff%",
    "InZone%"
]]

# ----------------------------
# Season Summary (FanGraphs)
# ----------------------------
st.write("## Season Summary")

if not fg_stats.empty and not player_row.empty:
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
    st.warning("No season summary available for this player and season.")

# ----------------------------
# Pitch Type Metrics
# ----------------------------
st.write("## Pitch Type Metrics")

# Pitch abbreviation legend — only show pitch types present in the data
active_pitches = metrics_df["pitch_type"].dropna().unique()
legend_items = [
    f"**{code}** — {pitch_name_mapping.get(code, code)}"
    for code in sorted(active_pitches)
]
with st.expander("Pitch Type Legend", expanded=False):
    cols = st.columns(3)
    for i, item in enumerate(legend_items):
        color = pitch_colors_mapping.get(item.split(" ")[0].strip("*"), "#888")
        cols[i % 3].markdown(
            f"<span style='color:{color}; font-size:14px'>●</span> {item}",
            unsafe_allow_html=True
        )

st.dataframe(
    metrics_df,
    column_config={
        "Pitch%": st.column_config.NumberColumn(format="%.1f%%"),
        "AvgVelo": st.column_config.NumberColumn(label="Avg Velo", format="%.1f mph"),
        "MaxVelo": st.column_config.NumberColumn(label="Max Velo", format="%.1f mph"),
        "Whiff%": st.column_config.NumberColumn(format="%.1f%%"),
        "InZone%": st.column_config.NumberColumn(format="%.1f%%")
    },
    use_container_width=True
)

release_df = (
    filtered_data
    .groupby("pitch_type")[["release_pos_x", "release_pos_z"]]
    .mean()
    .reset_index()
)

scale_factor = 5

# ----------------------------
# Pitch Movement Plot
# ----------------------------
# Start with a blank figure and add arm slot lines first
scatter_plot = go.Figure()

for pitch in filtered_data["pitch_type"].dropna().unique():
    pitch_df = filtered_data[filtered_data["pitch_type"] == pitch]
    scatter_plot.add_trace(
        go.Scatter(
            x=pitch_df["pfx_x"],
            y=pitch_df["pfx_z"],
            mode="markers",
            name=pitch,
            marker=dict(color=pitch_colors_mapping.get(pitch, "gray")),
            customdata=pitch_df[["release_speed", "pitch_type"]],
            hovertemplate="release_speed=%{customdata[0]}<br>pitch_type=%{customdata[1]}<extra></extra>"
        )
    )

# Add arm slot lines after — then reorder so they render beneath
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

# Reorder: move line traces to the front of the list (renders beneath)
n_pitches = filtered_data["pitch_type"].dropna().nunique()
n_lines = len(release_df)
reordered = (
    list(scatter_plot.data[n_pitches:]) +  # arm slot lines first
    list(scatter_plot.data[:n_pitches])     # scatter points on top
)
scatter_plot.data = reordered

scatter_plot.update_layout(title="Pitch Movement")
scatter_plot.update_xaxes(title="Horizontal Break (inches)", range=[25, -25])
scatter_plot.update_yaxes(title="Vertical Break (inches)", range=[-25, 25])

scatter_plot.add_hline(y=0, line_color="white", line_width=1)
scatter_plot.add_vline(x=0, line_color="white", line_width=1)

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
