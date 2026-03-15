import requests
from datetime import datetime, timezone
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd

st.set_page_config(page_title="Live Games", page_icon="⚾", layout="wide")

# ----------------------------
# Constants
# ----------------------------
BASE      = "https://statsapi.mlb.com/api/v1"
BASE_LIVE = "https://statsapi.mlb.com/api/v1.1"

PITCH_NAMES = {
    "FF": "4-Seam Fastball", "FA": "4-Seam Fastball",
    "SI": "Sinker",          "FT": "Sinker",
    "FC": "Cutter",
    "SL": "Slider",          "ST": "Sweeper",
    "CU": "Curveball",       "KC": "Knuckle-Curve",
    "CH": "Changeup",
    "FS": "Splitter",        "FO": "Forkball",
    "KN": "Knuckleball",
    "EP": "Eephus",
}

PITCH_COLORS = {
    "FF": "#FF007D", "FA": "#FF007D",
    "SI": "#98165D", "FT": "#98165D",
    "FC": "#BE5FA0",
    "SL": "#67E18D", "ST": "#1BB999",
    "CU": "#3025CE", "KC": "#311D8B",
    "CH": "#F79E70",
    "FS": "#FE6100", "FO": "#FE6100",
    "KN": "#867A08",
    "EP": "#648FFF",
}

def pitch_name(code):
    return PITCH_NAMES.get(code, code or "Unknown")

def pitch_color(code):
    return PITCH_COLORS.get(code, "#9C8975")

# ----------------------------
# API helpers
# ----------------------------
def get_et_today():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York")).date()
    except ImportError:
        from datetime import timedelta
        return (datetime.now(timezone.utc) - timedelta(hours=5)).date()

@st.cache_data(ttl=30)   # short TTL so live data stays fresh for today
def fetch_games(date_str: str, game_type: str) -> list:
    url = (
        f"{BASE}/schedule?sportId=1"
        f"&date={date_str}"
        f"&gameType={game_type}"
        f"&hydrate=teams,linescore,decisions,pitchers"
    )
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    data = r.json()
    games = []
    for d in data.get("dates", []):
        for g in d.get("games", []):
            games.append(g)
    return games

@st.cache_data(ttl=30)
def fetch_live_feed(game_pk: int) -> dict:
    url = f"{BASE_LIVE}/game/{game_pk}/feed/live"
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    return r.json()

# ----------------------------
# Data extraction
# ----------------------------
def abs_outs(inning, outs):
    """Convert inning + outs-in-inning to absolute out count from start of game."""
    return (int(inning) - 1) * 3 + int(outs)

def extract_pitchers(feed: dict) -> dict:
    """
    Extract all pitchers and their pitches from a live game feed.
    Returns dict: pitcher_id -> {name, team, pitches[], ip}
    """
    away_abbr = feed.get("gameData", {}).get("teams", {}).get("away", {}).get("abbreviation", "?")
    home_abbr = feed.get("gameData", {}).get("teams", {}).get("home", {}).get("abbreviation", "?")

    box = feed.get("liveData", {}).get("boxscore", {}).get("teams", {})
    away_ids = set(box.get("away", {}).get("pitchers", []))
    home_ids = set(box.get("home", {}).get("pitchers", []))

    pitcher_map = {}
    all_plays = feed.get("liveData", {}).get("plays", {}).get("allPlays", [])

    # First pass: collect pitches and track first/last play index per pitcher
    for i, play in enumerate(all_plays):
        pitcher = play.get("matchup", {}).get("pitcher")
        if not pitcher:
            continue
        pid   = pitcher["id"]
        pname = pitcher.get("fullName", "Unknown")

        if pid not in pitcher_map:
            if pid in away_ids:
                team = away_abbr
            elif pid in home_ids:
                team = home_abbr
            else:
                half = play.get("about", {}).get("halfInning", "")
                team = home_abbr if half == "top" else away_abbr
            pitcher_map[pid] = {
                "id": pid, "name": pname, "team": team, "pitches": [],
                "first_play_idx": i, "last_play_idx": i,
            }
        else:
            pitcher_map[pid]["last_play_idx"] = i

        batter    = play.get("matchup", {}).get("batter", {}).get("fullName", "")
        bat_side  = play.get("matchup", {}).get("batSide", {}).get("code", "?")
        inning    = play.get("about", {}).get("inning", 1)
        half      = "Top" if play.get("about", {}).get("halfInning") == "top" else "Bot"
        outs      = play.get("about", {}).get("startOuts", 0)
        away_sc   = play.get("result", {}).get("awayScore", "?")
        home_sc   = play.get("result", {}).get("homeScore", "?")
        scoreline = f"{away_abbr} {away_sc} - {home_sc} {home_abbr}"

        for ev in play.get("playEvents", []):
            if not ev.get("isPitch"):
                continue
            pd_    = ev.get("pitchData") or {}
            coords = pd_.get("coordinates", {})
            breaks = pd_.get("breaks", {})

            pitch_type = ev.get("details", {}).get("type", {}).get("code", "XX")
            velo       = pd_.get("startSpeed")
            raw_hbreak = breaks.get("breakHorizontal")
            raw_vbreak = breaks.get("breakVerticalInduced") or breaks.get("breakVertical")
            pfx_x      = (-raw_hbreak) if raw_hbreak is not None else pd_.get("pfxX")
            pfx_z      = raw_vbreak    if raw_vbreak  is not None else pd_.get("pfxZ")
            p_x        = coords.get("pX")
            p_z        = coords.get("pZ")
            rel_x      = coords.get("x0")
            rel_z      = coords.get("z0")
            spin_rate  = breaks.get("spinRate")
            result     = ev.get("details", {}).get("description", "")
            balls      = ev.get("count", {}).get("balls", "?")
            strikes    = ev.get("count", {}).get("strikes", "?")

            pitcher_map[pid]["pitches"].append({
                "pitch_type": pitch_type,
                "pitch_name": pitch_name(pitch_type),
                "velo":       round(velo,  1) if velo  is not None else None,
                "pfx_x":      round(pfx_x, 2) if pfx_x is not None else None,
                "pfx_z":      round(pfx_z, 2) if pfx_z is not None else None,
                "p_x":        round(p_x,   2) if p_x   is not None else None,
                "p_z":        round(p_z,   2) if p_z   is not None else None,
                "rel_x":      round(rel_x, 2) if rel_x is not None else None,
                "rel_z":      round(rel_z, 2) if rel_z is not None else None,
                "spin_rate":  round(spin_rate) if spin_rate is not None else None,
                "result":     result,
                "balls":      balls,
                "strikes":    strikes,
                "batter":     batter,
                "bat_side":   bat_side,
                "inning":     inning,
                "half":       half,
                "outs":       outs,
                "scoreline":  scoreline,
            })

    def end_outs(play):
        """Outs at END of a play = last outs value in playEvents, else about.outs."""
        last = None
        for ev in play.get("playEvents", []):
            val = ev.get("count", {}).get("outs")
            if val is not None:
                last = int(val)
        if last is not None:
            return last
        return int(play.get("about", {}).get("outs", 0))

    # Second pass: compute IP per pitcher
    # entry outs = end_outs of play immediately before pitcher's first play (0 if none)
    # exit outs  = end_outs of pitcher's last play
    for pid, p in pitcher_map.items():
        first_idx  = p["first_play_idx"]
        last_idx   = p["last_play_idx"]
        first_play = all_plays[first_idx]
        last_play  = all_plays[last_idx]

        entry_inning = int(first_play.get("about", {}).get("inning", 1))
        if first_idx > 0:
            prev_play    = all_plays[first_idx - 1]
            entry_inning = int(prev_play.get("about", {}).get("inning", entry_inning))
            entry_outs   = end_outs(prev_play)
        else:
            entry_outs   = 0  # started from the very first play of the game

        exit_inning = int(last_play.get("about", {}).get("inning", 1))
        exit_outs   = end_outs(last_play)

        total_outs = abs_outs(exit_inning, exit_outs) - abs_outs(entry_inning, entry_outs)
        total_outs = max(total_outs, 0)
        whole  = total_outs // 3
        thirds = total_outs  % 3
        p["ip"] = f"{whole}.{thirds}" if thirds else str(whole)

    return pitcher_map

def game_status_label(game: dict) -> str:
    state  = game.get("status", {}).get("abstractGameState", "")
    detail = game.get("status", {}).get("detailedState", "")
    if state == "Live":
        ls      = game.get("linescore", {})
        top     = ls.get("isTopInning", True)
        inning  = ls.get("currentInning", "?")
        half    = "▲" if top else "▼"
        a_runs  = game.get("linescore", {}).get("teams", {}).get("away", {}).get("runs", "?")
        h_runs  = game.get("linescore", {}).get("teams", {}).get("home", {}).get("runs", "?")
        return f"🔴 LIVE  {a_runs}–{h_runs}  {half}{inning}"
    if state == "Final":
        a_runs = game.get("linescore", {}).get("teams", {}).get("away", {}).get("runs", "")
        h_runs = game.get("linescore", {}).get("teams", {}).get("home", {}).get("runs", "")
        return f"Final  {a_runs}–{h_runs}"
    # Pre-game: show start time in Eastern Time
    game_date = game.get("gameDate", "")
    if game_date:
        try:
            from zoneinfo import ZoneInfo
            dt = datetime.fromisoformat(game_date.replace("Z", "+00:00"))
            et = dt.astimezone(ZoneInfo("America/New_York"))
            return et.strftime("%-I:%M %p ET")
        except Exception:
            pass
    return detail or "Scheduled"

# ----------------------------
# Page layout
# ----------------------------
st.title("⚾ Live Games")

# Sidebar controls
st.sidebar.header("Game Settings")

et_today = get_et_today()

selected_date = st.sidebar.date_input(
    "Date",
    value=et_today,
    max_value=et_today,
    min_value=et_today.replace(year=2008),
)

GAME_TYPE_LABELS = {
    "R": "Regular Season",
    "S": "Spring Training",
    "F": "Wild Card",
    "D": "Division Series",
    "L": "League Championship",
    "W": "World Series",
}
# Request all game types at once — no user selection needed
game_type = "R,S,F,D,L,W"

if st.sidebar.button("↻ Refresh"):
    st.cache_data.clear()

# ----------------------------
# Load games for selected date
# ----------------------------
date_str = selected_date.strftime("%Y-%m-%d")
is_today = (selected_date == et_today)

try:
    with st.spinner("Loading games..."):
        games = fetch_games(date_str, game_type)
except Exception as e:
    st.error(f"Could not load games: {e}")
    st.stop()

date_label = selected_date.strftime("%A, %B %-d, %Y")
st.caption(f"{'Today — ' if is_today else ''}{date_label}")

if not games:
    st.info(f"No games found for {date_label}. Try a different date.")
    st.stop()

# ----------------------------
# Game selector
# ----------------------------
def get_team_abbr(game, side):
    """Extract team abbreviation with multiple fallback paths."""
    teams = game.get("teams", {})
    side_data = teams.get(side, {})
    # Path 1: teams.away.team.abbreviation (hydrated)
    abbr = side_data.get("team", {}).get("abbreviation")
    if abbr:
        return abbr
    # Path 2: teams.away.abbreviation (some responses)
    abbr = side_data.get("abbreviation")
    if abbr:
        return abbr
    # Path 3: fall back to team name initials
    name = side_data.get("team", {}).get("name") or side_data.get("name")
    if name:
        return name[:3].upper()
    return "?"

def game_label(game):
    away = get_team_abbr(game, "away")
    home = get_team_abbr(game, "home")
    status = game_status_label(game)
    return f"{away} @ {home}  —  {status}"

game_options = {game_label(g): g for g in games}

selected_game_label = st.selectbox(
    "Select Game",
    options=list(game_options.keys()),
    index=0,
)
selected_game = game_options[selected_game_label]
game_pk = selected_game["gamePk"]

# ----------------------------
# Load game feed
# ----------------------------
try:
    with st.spinner("Loading pitch data..."):
        feed = fetch_live_feed(game_pk)
except Exception as e:
    st.error(f"Could not load game data: {e}")
    st.stop()

pitcher_map = extract_pitchers(feed)

away = selected_game.get("teams", {}).get("away", {}).get("team", {}) or selected_game.get("teams", {}).get("away", {})
home = selected_game.get("teams", {}).get("home", {}).get("team", {}) or selected_game.get("teams", {}).get("home", {})
away_abbr = get_team_abbr(selected_game, "away")
home_abbr = get_team_abbr(selected_game, "home")
is_live   = selected_game.get("status", {}).get("abstractGameState") == "Live"

# Game header
state_label = game_status_label(selected_game)
st.subheader(f"{away.get('name','?')} @ {home.get('name','?')}")
st.caption(f"{state_label}  ·  {selected_game.get('venue',{}).get('name','')}"
           + ("  ·  Auto-refresh: reload page for live updates" if is_live else ""))

pitchers_with_pitches = {
    pid: p for pid, p in pitcher_map.items() if len(p["pitches"]) > 0
}

if not pitchers_with_pitches:
    status_state = selected_game.get("status", {}).get("abstractGameState", "")
    if status_state == "Preview":
        st.info("⏰ Game hasn't started yet — no pitch data available.")
    else:
        st.info("📊 No pitch data found for this game.")
    st.stop()

# ----------------------------
# Team pitching summary tables
# ----------------------------
def player_link(name):
    """Return an HTML anchor linking to the season stats page pre-filled with the player name."""
    from urllib.parse import quote
    encoded = quote(name)
    url = "/Pitch_Plots?player=" + encoded
    return '<a href="' + url + '" target="_self">' + name + '</a>'

def fmt(val, suffix=""):
    return f"{val}{suffix}" if val is not None else "—"

def build_and_render_team_section(team_abbr, team_name, pitcher_list):
    if not pitcher_list:
        return

    pitcher_list.sort(key=lambda p: -len(p["pitches"]))

    swing_results  = {"Swinging Strike", "Swinging Strike (Blocked)", "Foul", "Foul Tip",
                      "In play, out(s)", "In play, no out", "In play, runs"}
    whiff_results  = {"Swinging Strike", "Swinging Strike (Blocked)"}
    strike_results = {"Called Strike", "Swinging Strike", "Swinging Strike (Blocked)",
                      "Foul", "Foul Tip", "In play, out(s)", "In play, no out", "In play, runs"}

    headers = ["Pitcher", "IP", "Pitches", "Avg Velo", "Max Velo", "Whiffs", "Strikes", "Balls", "InZone%", "Arsenal"]
    header_row = "".join(f"<th>{h}</th>" for h in headers)

    rows_html = ""
    for p in pitcher_list:
        pitches  = p["pitches"]
        total    = len(pitches)
        velos    = [x["velo"] for x in pitches if x["velo"] is not None]
        avg_velo = fmt(round(sum(velos)/len(velos), 1), " mph") if velos else "—"
        max_velo = fmt(round(max(velos), 1), " mph") if velos else "—"
        whiffs   = sum(1 for x in pitches if x["result"] in whiff_results)
        strikes  = sum(1 for x in pitches if (x["result"] or "") in strike_results)
        balls    = sum(1 for x in pitches if (x["result"] or "").lower().startswith("ball"))
        in_zone  = sum(1 for x in pitches
                       if x.get("p_z") is not None and 1.5 <= x["p_z"] <= 3.5
                       and x.get("p_x") is not None and -0.83 <= x["p_x"] <= 0.83)
        in_zone_pct = fmt(round(in_zone / total * 100, 1), "%") if total else "—"

        type_counts = {}
        for x in pitches:
            type_counts[x["pitch_type"]] = type_counts.get(x["pitch_type"], 0) + 1
        arsenal = ", ".join(
            f"{pt} {cnt/total*100:.0f}%"
            for pt, cnt in sorted(type_counts.items(), key=lambda i: -i[1])[:5]
        )

        cells = [
            player_link(p["name"]),
            p.get("ip", "—"), total, avg_velo, max_velo, whiffs, strikes, balls, in_zone_pct, arsenal
        ]
        rows_html += "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"

    css = """
    <style>
      .summary-table {
        width: 100%; border-collapse: collapse; font-size: 13px; margin-bottom: 16px;
      }
      .summary-table th {
        text-align: left; padding: 6px 10px; border-bottom: 2px solid #444;
        font-family: monospace; font-size: 11px; color: #aaa; text-transform: uppercase;
      }
      .summary-table td {
        padding: 6px 10px; border-bottom: 1px solid #2a2a2a;
      }
      .summary-table a {
        color: #c8f135; text-decoration: none; font-weight: 500;
      }
      .summary-table a:hover { text-decoration: underline; }
      .summary-table tr:hover td { background: rgba(200,241,53,0.04); }
    </style>
    """
    table_html = (
        css
        + "<table class=\"summary-table\">"
        + "<thead><tr>" + header_row + "</tr></thead>"
        + "<tbody>" + rows_html + "</tbody>"
        + "</table>"
    )
    st.markdown(f"#### {team_abbr} — {team_name} Pitching")
    st.markdown(table_html, unsafe_allow_html=True)

def show_team_section(team_abbr, team_name, pitcher_ids):
    team_pitchers = [
        pitchers_with_pitches[pid]
        for pid in pitcher_ids
        if pid in pitchers_with_pitches
    ]
    build_and_render_team_section(team_abbr, team_name, team_pitchers)

box = feed.get("liveData", {}).get("boxscore", {}).get("teams", {})
away_pitcher_ids = box.get("away", {}).get("pitchers", [])
home_pitcher_ids = box.get("home", {}).get("pitchers", [])

show_team_section(away_abbr, away.get("name", away_abbr), away_pitcher_ids)
show_team_section(home_abbr, home.get("name", home_abbr), home_pitcher_ids)

st.markdown("---")

# ----------------------------
# Pitcher drill-down
# ----------------------------
st.subheader("Pitcher Detail")

pitcher_options = {
    f"{p['name']} ({p['team']})  —  {len(p['pitches'])} pitches": pid
    for pid, p in sorted(
        pitchers_with_pitches.items(),
        key=lambda i: -len(i[1]["pitches"])
    )
}

selected_pitcher_label = st.selectbox(
    "Select Pitcher",
    options=list(pitcher_options.keys()),
)
selected_pid = pitcher_options[selected_pitcher_label]
selected_pitcher = pitchers_with_pitches[selected_pid]
pitches = selected_pitcher["pitches"]

df = pd.DataFrame(pitches)

# Pitch type filter
pitch_types = sorted(df["pitch_type"].dropna().unique())
selected_types = st.multiselect(
    "Filter by Pitch Type",
    options=pitch_types,
    default=pitch_types,
)
df = df[df["pitch_type"].isin(selected_types)]

# ----------------------------
# Metrics row
# ----------------------------
total   = len(df)
velos   = df["velo"].dropna()
avg_v   = f"{velos.mean():.1f} mph" if len(velos) else "—"
max_v   = f"{velos.max():.1f} mph"  if len(velos) else "—"

swing_r  = {"Swinging Strike", "Swinging Strike (Blocked)", "Foul", "Foul Tip",
            "In play, out(s)", "In play, no out", "In play, runs"}
whiff_r  = {"Swinging Strike", "Swinging Strike (Blocked)"}
strike_r = {"Called Strike", "Swinging Strike", "Swinging Strike (Blocked)",
             "Foul", "Foul Tip", "In play, out(s)", "In play, no out", "In play, runs"}
swings   = df["result"].isin(swing_r).sum()
whiffs   = int(df["result"].isin(whiff_r).sum())
strikes  = int(df["result"].isin(strike_r).sum())
balls    = int(df["result"].apply(lambda r: (r or "").lower().startswith("ball")).sum())
in_zone  = int(df[df["p_z"].between(1.5, 3.5) & df["p_x"].between(-0.83, 0.83)].shape[0]) if "p_z" in df else 0

in_zone_pct = f"{in_zone/total*100:.1f}%" if total else "—"

m1, m2, m3, m4, m5, m6, m7 = st.columns(7)
m1.metric("Pitches", total)
m2.metric("Avg Velo", avg_v)
m3.metric("Max Velo", max_v)
m4.metric("Whiffs", whiffs)
m5.metric("Strikes", strikes)
m6.metric("Balls", balls)
m7.metric("In Zone%", in_zone_pct)

# ----------------------------
# Pitch type breakdown table
# ----------------------------
if not df.empty:
    breakdown_rows = []
    for pt in sorted(df["pitch_type"].unique()):
        sub   = df[df["pitch_type"] == pt]
        n     = len(sub)
        v     = sub["velo"].dropna()
        sw    = sub["result"].isin(swing_r).sum()
        wh    = sub["result"].isin(whiff_r).sum()
        str_count = sub["result"].isin(strike_r).sum()
        bl_count  = sub["result"].apply(lambda r: (r or "").lower().startswith("ball")).sum()
        iz_count  = sub[sub["p_z"].between(1.5, 3.5) & sub["p_x"].between(-0.83, 0.83)].shape[0] if "p_z" in sub else 0
        breakdown_rows.append({
            "Pitch":     f"{pt} — {pitch_name(pt)}",
            "Count":     n,
            "Usage%":    round(n / total * 100, 1),
            "Avg Velo":  round(v.mean(), 1) if len(v) else None,
            "Max Velo":  round(v.max(),  1) if len(v) else None,
            "Whiffs":    int(wh),
            "Strikes":   int(str_count),
            "Balls":     int(bl_count),
            "InZone%":   round(iz_count  / n * 100, 1) if n else None,
        })
    breakdown_df = pd.DataFrame(breakdown_rows)
    st.dataframe(
        breakdown_df,
        column_config={
            "Usage%":   st.column_config.NumberColumn(format="%.1f%%"),
            "Avg Velo": st.column_config.NumberColumn(format="%.1f mph"),
            "Max Velo": st.column_config.NumberColumn(format="%.1f mph"),
            "InZone%":  st.column_config.NumberColumn(format="%.1f%%"),
        },
        use_container_width=True,
        hide_index=True,
    )

# ----------------------------
# Movement & Location plots
# ----------------------------
plot_df = df.dropna(subset=["pfx_x", "pfx_z"])
loc_df  = df.dropna(subset=["p_x", "p_z"])

col_left, col_right = st.columns(2)

with col_left:
    st.markdown("### Pitch Movement")
    if not plot_df.empty:
        # Compute mean release position per pitch type for arm slot lines
        release_df = (
            plot_df.dropna(subset=["rel_x", "rel_z"])
            .groupby("pitch_type")[["rel_x", "rel_z"]]
            .mean()
            .reset_index()
        )
        scale_factor = 5

        fig = go.Figure()

        # Add scatter traces first
        for pt in plot_df["pitch_type"].unique():
            sub = plot_df[plot_df["pitch_type"] == pt]
            fig.add_trace(go.Scatter(
                x=sub["pfx_x"], y=sub["pfx_z"],
                mode="markers",
                name=f"{pt} — {pitch_name(pt)}",
                marker=dict(color=pitch_color(pt), size=8, opacity=0.8),
                customdata=sub[["velo", "result", "batter"]],
                hovertemplate=(
                    "<b>%{fullData.name}</b><br>"
                    "Velo: %{customdata[0]} mph<br>"
                    "Result: %{customdata[1]}<br>"
                    "Batter: %{customdata[2]}<extra></extra>"
                ),
            ))

        # Add arm slot lines
        for _, row in release_df.iterrows():
            pt    = row["pitch_type"]
            x_val = row["rel_x"] * scale_factor
            z_val = row["rel_z"] * scale_factor
            fig.add_trace(go.Scatter(
                x=[0, x_val], y=[0, z_val],
                mode="lines",
                line=dict(dash="dash", width=3, color=pitch_color(pt)),
                name=f"{pt} Arm Slot",
                showlegend=False,
            ))

        # Reorder: arm slot lines beneath scatter points
        n_pitches = plot_df["pitch_type"].nunique()
        reordered = list(fig.data[n_pitches:]) + list(fig.data[:n_pitches])
        fig.data = reordered

        fig.add_hline(y=0, line_color="white", line_width=1)
        fig.add_vline(x=0, line_color="white", line_width=1)
        fig.update_xaxes(title="Horizontal Break (in)", range=[25, -25])
        fig.update_yaxes(title="Vertical Break (in)", range=[-25, 25])
        fig.update_layout(height=420, legend=dict(orientation="h", y=-0.2))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No movement data available yet.")

with col_right:
    st.markdown("### Pitch Location")
    if not loc_df.empty:
        fig2 = px.scatter(
            loc_df, x="p_x", y="p_z",
            color="pitch_type",
            color_discrete_map=PITCH_COLORS,
            hover_data=["velo", "result", "batter", "pitch_name"],
            labels={"p_x": "Horizontal", "p_z": "Height", "pitch_type": "Pitch"},
        )
        # Strike zone
        fig2.add_shape(type="rect", x0=-0.83, x1=0.83, y0=1.5, y1=3.5,
                       line=dict(color="white", width=2))
        fig2.update_xaxes(range=[2, -2])
        fig2.update_yaxes(range=[0, 6])
        fig2.update_layout(height=420, legend=dict(orientation="h", y=-0.2))
        st.plotly_chart(fig2, use_container_width=True)
    else:
        st.info("No location data available yet.")

# ----------------------------
# Pitch log table
# ----------------------------
with st.expander("Pitch Log", expanded=False):
    log_cols = ["inning", "half", "batter", "bat_side", "pitch_type",
                "pitch_name", "velo", "pfx_x", "pfx_z", "balls", "strikes", "result"]
    available_cols = [c for c in log_cols if c in df.columns]
    log_df = df[available_cols].copy()
    log_df.columns = [c.replace("_", " ").title() for c in log_df.columns]
    st.dataframe(log_df, use_container_width=True, hide_index=True)
