import pandas as pd
import numpy as np
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from pybaseball import statcast_pitcher, playerid_lookup, pitching_stats
from pybaseball.playerid_lookup import get_lookup_table

st.set_page_config(page_title="Pitch Analysis", page_icon="⚾", layout="wide")

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
@st.cache_data(persist="disk")
def get_filtered_data(start_date, end_date, playerid):
    return statcast_pitcher(start_dt=start_date, end_dt=end_date, player_id=playerid)

@st.cache_data(persist="disk")
def get_season_stats(season):
    try:
        stats = pitching_stats(season, qual=0)
        if stats is None or stats.empty:
            return pd.DataFrame()
        return stats
    except Exception:
        return pd.DataFrame()

@st.cache_data(persist="disk")
def load_lookup_table():
    """
    Load the full Chadwick player register once and cache it to disk.
    This powers all search queries locally without repeated API calls.
    """
    return get_lookup_table()

@st.cache_data(persist="disk")
def get_available_seasons(playerid):
    """
    Fetch all Statcast data in a single request and extract unique seasons.
    Much faster than probing each year individually.
    """
    current_year = pd.Timestamp.now().year
    try:
        all_data = statcast_pitcher(
            start_dt=f"2015-03-01",
            end_dt=f"{current_year}-11-01",
            player_id=playerid
        )
        if all_data is None or all_data.empty:
            return []
        return sorted(
            all_data["game_year"].dropna().astype(int).unique().tolist(),
            reverse=True
        )
    except Exception:
        return []

def get_arm_angle(data):
    """
    Extract the Savant arm_angle from the statcast data.
    Returns the median arm angle as a float, or None if unavailable
    (pre-2020 seasons or column absent).
    """
    if "arm_angle" not in data.columns:
        return None
    angles = data["arm_angle"].dropna()
    if angles.empty:
        return None
    return round(float(angles.median()), 1)


# ----------------------------
# App Title
# ----------------------------
st.title("Pitch Movement & Season Dashboard")

# ----------------------------
# Load Lookup Table Once
# ----------------------------
with st.spinner("Loading player database..."):
    lookup_table = load_lookup_table()

# Hide sidebar, show inline nav
st.markdown("""
    <style>
        html, body, [data-testid="stApp"] { background-color: #0e1117 !important; }
        [data-testid="stSidebar"]        { display: none; }
        [data-testid="collapsedControl"] { display: none; }
        .nav-bar { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }
        .nav-bar a {
            padding: 6px 18px; border-radius: 20px; text-decoration: none;
            font-size: 14px; font-weight: 500;
            background: rgba(255,255,255,0.07);
            color: #ccc; border: 1px solid rgba(255,255,255,0.12);
        }
        .nav-bar a:hover { background: rgba(255,255,255,0.15); color: #fff; }
        .nav-bar a.active {
            background: #c8f135; color: #111;
            border-color: #c8f135; font-weight: 700;
        }
    </style>
    <div class="nav-bar">
        <a href="/Season_Data" target="_self" class="active">⚾ Season Stats</a>
        <a href="/Live_Games"  target="_self" >🔴 Live Games</a>
        <a href="/Compare"     target="_self" >📊 Compare</a>
    </div>
""", unsafe_allow_html=True)

# ----------------------------
# FIND PITCHER — inline top controls
# ----------------------------
import unicodedata

def strip_accents(text):
    return unicodedata.normalize("NFD", text).encode("ascii", "ignore").decode("ascii")

# Build lookup: norm_label -> player info + pretty display label
named_id_mapping = {}
norm_to_pretty = {}

valid_players = lookup_table[
    lookup_table["key_mlbam"].notna() & (lookup_table["key_mlbam"] != "")
].drop_duplicates(subset=["key_mlbam"]).copy()

for _, row in valid_players.iterrows():
    first = str(row["name_first"]).title()
    last = str(row["name_last"]).title()
    first_year = int(row["mlb_played_first"]) if pd.notna(row.get("mlb_played_first")) else None
    last_year = int(row["mlb_played_last"]) if pd.notna(row.get("mlb_played_last")) else None
    year_str = f" ({first_year}–{last_year})" if first_year else ""
    display_label = f"{first} {last}{year_str}"
    norm_label = strip_accents(display_label)
    if norm_label not in named_id_mapping:
        named_id_mapping[norm_label] = {
            "mlbam": row["key_mlbam"],
            "fangraphs": row["key_fangraphs"],
            "display_name": f"{first} {last}",
        }
        norm_to_pretty[norm_label] = display_label

_param_player = st.query_params.get("player", "")

ctrl_c1, ctrl_c2, ctrl_c3 = st.columns([2, 2, 1])

with ctrl_c1:
    search_query = st.text_input(
        "🔍 Search pitcher",
        value=_param_player,
        placeholder="e.g. Greene, Lodolo",
    )

if not search_query or len(search_query.strip()) < 2:
    st.info("Search for a pitcher above to get started.")
    st.stop()

query_norm = strip_accents(search_query.strip()).lower()
matched_norms = sorted([n for n in named_id_mapping if query_norm in n.lower()])

if not matched_norms:
    st.error("No players found. Try a different spelling.")
    st.stop()

with ctrl_c2:
    selected_norm = st.selectbox(
        "Select pitcher",
        options=matched_norms,
        format_func=lambda n: norm_to_pretty[n],
        index=0,
        label_visibility="visible",
    )

if selected_norm is None:
    st.stop()

playerid = named_id_mapping[selected_norm]["mlbam"]
fangraphs_id = named_id_mapping[selected_norm]["fangraphs"]
selected_player_name = named_id_mapping[selected_norm]["display_name"]

# ----------------------------
# Season / Year Selection
# ----------------------------
with st.spinner("Finding available seasons..."):
    available_years = get_available_seasons(playerid)

if not available_years:
    st.error("No Statcast data found for this pitcher.")
    st.stop()

with ctrl_c3:
    season = st.selectbox("Season", options=available_years, index=0)

st.markdown("---")

# ----------------------------
# Fetch Season Stats
# ----------------------------
fg_stats = get_season_stats(season)

if not fg_stats.empty:
    player_row = fg_stats[fg_stats["Name"].apply(lambda n: strip_accents(n).lower()) == strip_accents(selected_player_name).lower()]
else:
    player_row = pd.DataFrame()

# ----------------------------
# Season date window (hardcoded — avoids an extra Baseball Reference scrape)
# ----------------------------
start_date = f"{season}-03-20"
end_date   = f"{season}-10-05"
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

# Filter to the selected season only (the persisted cache may cover multiple years)
if "game_year" in data.columns:
    data = data[data["game_year"] == season]

if data.empty:
    st.warning(f"No data found for {season}.")
    st.stop()

st.success(f"Data loaded for {season} regular season.")

# ----------------------------
# Convert Movement to Inches
# ----------------------------
data["pfx_x"] = data["pfx_x"] * 12
data["pfx_z"] = data["pfx_z"] * 12

# ----------------------------
# Extract Arm Angle
# ----------------------------
arm_angle = get_arm_angle(data)

# ----------------------------
# Pitch Type Filter + Batter Handedness
# ----------------------------
pitch_types = sorted(data["pitch_type"].dropna().unique())

filter_col1, filter_col2 = st.columns([3, 1])

with filter_col1:
    selected_pitches = st.multiselect(
        "Filter by Pitch Type",
        options=pitch_types,
        default=pitch_types
    )

with filter_col2:
    batter_hand = st.radio(
        "Batter",
        options=["Both", "R", "L"],
        horizontal=True,
        label_visibility="visible",
    )

filtered_data = data[data["pitch_type"].isin(selected_pitches)].copy()

# Apply batter handedness filter
if batter_hand != "Both" and "stand" in filtered_data.columns:
    filtered_data = filtered_data[filtered_data["stand"] == batter_hand]

if filtered_data.empty:
    hand_label = "right-handed" if batter_hand == "R" else "left-handed"
    st.info(f"No pitches found vs {hand_label} batters for the selected filters.")
    st.stop()

# Ensure optional columns exist — some seasons / venues omit these
for _col in ["batter_name", "outs_when_up"]:
    if _col not in filtered_data.columns:
        filtered_data[_col] = "?"

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
    "swinging_strike_blocked",
    "foul_tip",
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
    summary_data = {
        "Season": [season],
        "IP": [round(player_row["IP"].values[0], 1)],
        "ERA": [round(player_row["ERA"].values[0], 2)],
        "FIP": [round(player_row["FIP"].values[0], 2)],
        "K%": [round(player_row["K%"].values[0] * 100, 1)],
        "BB%": [round(player_row["BB%"].values[0] * 100, 1)],
    }
    if arm_angle is not None:
        summary_data["Arm Angle"] = [f"{arm_angle}°"]
    summary_df = pd.DataFrame(summary_data)
    st.dataframe(summary_df, use_container_width=True)
else:
    if arm_angle is not None:
        st.metric("Arm Angle", f"{arm_angle}°")
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

# ----------------------------
# Pitch Usage by Count
# ----------------------------
count_data = filtered_data.dropna(subset=["balls", "strikes", "pitch_type"]).copy()
count_data["balls"]   = count_data["balls"].astype(int)
count_data["strikes"] = count_data["strikes"].astype(int)
count_data["count"]   = count_data["balls"].astype(str) + "-" + count_data["strikes"].astype(str)

ALL_COUNTS = ["0-0", "1-0", "2-0", "3-0", "0-1", "1-1", "2-1", "3-1", "0-2", "1-2", "2-2", "3-2"]
present_counts = [c for c in ALL_COUNTS if c in count_data["count"].values]

if not count_data.empty and present_counts:
    count_pivot = (
        count_data.groupby(["count", "pitch_type"])
        .size()
        .reset_index(name="n")
    )
    count_totals = count_data.groupby("count").size().reset_index(name="total")
    count_pivot  = count_pivot.merge(count_totals, on="count")
    count_pivot["pct"] = (count_pivot["n"] / count_pivot["total"] * 100).round(1)

    pitch_order = (
        count_data.groupby("pitch_type")
        .size()
        .sort_values(ascending=False)
        .index.tolist()
    )

    table_rows = []
    for cnt in present_counts:
        sub = count_pivot[count_pivot["count"] == cnt]
        total_pitches = count_totals.loc[count_totals["count"] == cnt, "total"].values
        total_n = int(total_pitches[0]) if len(total_pitches) else 0
        row = {"Count": cnt, "Total": total_n}
        for pt in pitch_order:
            match = sub[sub["pitch_type"] == pt]
            row[pt] = match["pct"].values[0] if not match.empty else 0.0
        table_rows.append(row)

    count_df = pd.DataFrame(table_rows)

    with st.expander("Pitch Usage by Count", expanded=False):
        st.caption("% of pitches thrown in each count. Red = higher usage, blue = lower usage (per pitch type).")

        # Build per-column min/max for normalizing colors
        col_ranges = {}
        for pt in pitch_order:
            col_vals = count_df[pt].values
            col_ranges[pt] = (col_vals.min(), col_vals.max())

        def _cell_color(val, col_min, col_max):
            if col_max == col_min:
                return "rgba(120,120,120,0.15)"
            t = (val - col_min) / (col_max - col_min)   # 0=lowest, 1=highest
            if t >= 0.5:
                opacity = 0.15 + (t - 0.5) * 2 * 0.60
                return f"rgba(210,50,50,{opacity:.2f})"
            else:
                opacity = 0.15 + (0.5 - t) * 2 * 0.60
                return f"rgba(50,100,210,{opacity:.2f})"

        header_cells = "<th>Count</th><th>Total</th>" + "".join(
            f"<th title='{pitch_name_mapping.get(pt, pt)}'>{pt}</th>" for pt in pitch_order
        )
        rows_html = ""
        for _, row in count_df.iterrows():
            cells = (
                f"<td style='font-weight:600'>{row['Count']}</td>"
                f"<td style='color:#aaa'>{int(row['Total'])}</td>"
            )
            for pt in pitch_order:
                val = row[pt]
                col_min, col_max = col_ranges[pt]
                bg = _cell_color(val, col_min, col_max)
                cells += f"<td style='background:{bg};text-align:right'>{val:.1f}%</td>"
            rows_html += f"<tr>{cells}</tr>"

        css = """
        <style>
          .count-table {
            width:100%; border-collapse:collapse; font-size:13px; margin-bottom:8px;
          }
          .count-table th {
            text-align:center; padding:5px 10px; border-bottom:2px solid #444;
            font-family:monospace; font-size:11px; color:#aaa; text-transform:uppercase;
          }
          .count-table td { padding:5px 10px; border-bottom:1px solid #1e1e1e; text-align:center; }
        </style>
        """
        st.markdown(
            css
            + "<table class='count-table'><thead><tr>"
            + header_cells
            + "</tr></thead><tbody>"
            + rows_html
            + "</tbody></table>",
            unsafe_allow_html=True,
        )

# ----------------------------
# Pitch Movement Plot
# ----------------------------
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

# White × marker at each pitch type's movement centroid
for pitch in filtered_data["pitch_type"].dropna().unique():
    pfx_sub = filtered_data[filtered_data["pitch_type"] == pitch][["pfx_x", "pfx_z"]].dropna()
    if pfx_sub.empty:
        continue
    cx = pfx_sub["pfx_x"].mean()
    cz = pfx_sub["pfx_z"].mean()
    scatter_plot.add_trace(
        go.Scatter(
            x=[cx],
            y=[cz],
            mode="markers+text",
            marker=dict(
                symbol="x",
                size=14,
                color="white",
                line=dict(width=2, color="white"),
            ),
            text=[pitch],
            textposition="top center",
            textfont=dict(size=10, color="white"),
            showlegend=False,
            hovertemplate=(
                pitch + "<br>Avg HB: %{x:.1f} in<br>Avg VB: %{y:.1f} in<extra></extra>"
            ),
        )
    )

# Title includes arm angle when available
movement_title = "Pitch Movement"
if arm_angle is not None:
    movement_title += f"  ·  Arm Angle: {arm_angle}°"
elif season < 2020:
    movement_title += "  ·  Arm angle data available from 2020+"

scatter_plot.update_layout(title=movement_title)
scatter_plot.update_xaxes(title="Horizontal Break (inches)", range=[25, -25])
scatter_plot.update_yaxes(title="Vertical Break (inches)", range=[-25, 25])
scatter_plot.add_hline(y=0, line_color="white", line_width=1)
scatter_plot.add_vline(x=0, line_color="white", line_width=1)

# ----------------------------
# Pitch Location Plot
# ----------------------------
loc_data = filtered_data.dropna(subset=["plate_x", "plate_z"])

scatter_plot_2 = go.Figure()
for pitch in loc_data["pitch_type"].dropna().unique():
    pitch_df = loc_data[loc_data["pitch_type"] == pitch]
    scatter_plot_2.add_trace(go.Scatter(
        x=pitch_df["plate_x"],
        y=pitch_df["plate_z"],
        mode="markers",
        name=pitch,
        marker=dict(color=pitch_colors_mapping.get(pitch, "gray"), size=7, opacity=0.7),
        customdata=pitch_df[["release_speed", "balls", "strikes", "inning", "outs_when_up", "batter_name"]],
        hovertemplate=(
            "<b>%{fullData.name}</b><br>"
            "Batter: %{customdata[5]}<br>"
            "Count: %{customdata[1]}-%{customdata[2]}<br>"
            "Inning: %{customdata[3]}, %{customdata[4]} out<br>"
            "Velo: %{customdata[0]} mph<extra></extra>"
        ),
    ))

scatter_plot_2.add_shape(
    type="rect",
    x0=-0.83, x1=0.83,
    y0=1.5, y1=3.5,
    line=dict(color="white", width=2)
)

scatter_plot_2.update_xaxes(
    title="Horizontal (ft)", range=[2, -2], constrain="domain"
)
scatter_plot_2.update_yaxes(
    title="Height (ft)", range=[0, 6], scaleanchor="x", scaleratio=1
)
scatter_plot_2.update_layout(height=520, legend=dict(orientation="h", y=-0.15))

st.markdown("---")

col1, col2 = st.columns(2)

with col1:
    st.write("### Pitch Movement")
    st.plotly_chart(scatter_plot, use_container_width=True)

with col2:
    st.write("### Pitch Location")
    st.plotly_chart(scatter_plot_2, use_container_width=True)

# ----------------------------
# Avg Velocity by Inning
# ----------------------------
velo_inning_data = filtered_data.dropna(subset=["release_speed", "inning"])

if not velo_inning_data.empty:
    st.write("### Avg Velocity by Inning")

    inning_fig = go.Figure()

    for pitch in sorted(velo_inning_data["pitch_type"].dropna().unique()):
        sub = velo_inning_data[velo_inning_data["pitch_type"] == pitch]
        avg_by_inning = (
            sub.groupby("inning")["release_speed"]
            .agg(avg_velo="mean", count="count")
            .reset_index()
            .sort_values("inning")
        )
        avg_by_inning["avg_velo"] = avg_by_inning["avg_velo"].round(1)

        inning_fig.add_trace(go.Scatter(
            x=avg_by_inning["inning"],
            y=avg_by_inning["avg_velo"],
            mode="lines+markers",
            name=pitch,
            line=dict(color=pitch_colors_mapping.get(pitch, "gray"), width=2),
            marker=dict(color=pitch_colors_mapping.get(pitch, "gray"), size=8),
            customdata=avg_by_inning[["count"]],
            hovertemplate=(
                "<b>%{fullData.name}</b><br>"
                "Inning: %{x}<br>"
                "Avg Velo: %{y} mph<br>"
                "Pitches: %{customdata[0]}<extra></extra>"
            ),
        ))

    all_innings = sorted(velo_inning_data["inning"].dropna().unique())
    inning_fig.update_xaxes(
        title="Inning",
        tickmode="array",
        tickvals=all_innings,
        ticktext=[str(int(i)) for i in all_innings],
        showgrid=False,
    )
    inning_fig.update_yaxes(
        title="Avg Velocity (mph)",
        showgrid=True,
        gridcolor="rgba(255,255,255,0.08)",
    )
    inning_fig.update_layout(
        height=350,
        legend=dict(orientation="h", y=-0.2),
        margin=dict(t=30),
    )
    st.plotly_chart(inning_fig, use_container_width=True)

# ----------------------------
# Spin Rate & Axis
# ----------------------------
spin_data = filtered_data.dropna(subset=["release_spin_rate", "spin_axis"])

if not spin_data.empty:
    st.write("### Spin Rate & Axis")

    spin_fig = go.Figure()

    for pitch in sorted(spin_data["pitch_type"].dropna().unique()):
        sub = spin_data[spin_data["pitch_type"] == pitch]
        spin_fig.add_trace(go.Scatter(
            x=sub["spin_axis"],
            y=sub["release_spin_rate"],
            mode="markers",
            name=pitch,
            marker=dict(color=pitch_colors_mapping.get(pitch, "gray"), size=7, opacity=0.75),
            customdata=sub[["release_speed", "batter_name"]],
            hovertemplate=(
                "<b>%{fullData.name}</b><br>"
                "Spin Rate: %{y} rpm<br>"
                "Spin Axis: %{x}°<br>"
                "Velo: %{customdata[0]} mph<br>"
                "Batter: %{customdata[1]}<extra></extra>"
            ),
        ))

    spin_fig.update_xaxes(
        title="Spin Axis (°)",
        range=[0, 360],
        tickvals=[0, 90, 180, 270, 360],
        ticktext=["0°", "90°", "180°", "270°", "360°"],
        showgrid=True,
        gridcolor="rgba(255,255,255,0.08)",
    )
    spin_fig.update_yaxes(
        title="Spin Rate (rpm)",
        showgrid=True,
        gridcolor="rgba(255,255,255,0.08)",
    )
    spin_fig.update_layout(
        height=400,
        legend=dict(orientation="h", y=-0.2),
        margin=dict(t=30),
    )
    st.plotly_chart(spin_fig, use_container_width=True)
