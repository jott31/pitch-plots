import requests
import threading
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
def fetch_games(date_str: str, game_type: str, sport_id: int = 1) -> list:
    url = (
        f"{BASE}/schedule?sportId={sport_id}"
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

def prefetch_feed_background(game_pk: int):
    """
    Fire off fetch_live_feed in a background thread so the data is
    warming in Streamlit's cache while the user reads the game selector.
    The result is stored in session_state as a fallback if the cache
    isn't ready yet, but usually the thread finishes before the user picks.
    """
    def _fetch():
        try:
            result = fetch_live_feed(game_pk)
            st.session_state[f"prefetch_{game_pk}"] = result
        except Exception:
            pass

    key = f"prefetch_started_{game_pk}"
    if key not in st.session_state:
        st.session_state[key] = True
        t = threading.Thread(target=_fetch, daemon=True)
        t.start()

# ----------------------------
# Data extraction
# ----------------------------
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
            pitcher_map[pid] = {"id": pid, "name": pname, "team": team, "pitches": [], "first_play_idx": i}

        batter    = play.get("matchup", {}).get("batter", {}).get("fullName", "")
        bat_side  = play.get("matchup", {}).get("batSide", {}).get("code", "?")
        inning    = play.get("about", {}).get("inning", 1)
        half      = "Top" if play.get("about", {}).get("halfInning") == "top" else "Bot"
        outs      = play.get("about", {}).get("startOuts", 0)
        away_sc   = play.get("result", {}).get("awayScore", "?")
        home_sc   = play.get("result", {}).get("homeScore", "?")
        scoreline = f"{away_abbr} {away_sc} - {home_sc} {home_abbr}"

        # Track the pre-pitch count by walking events in order.
        # ev["count"] is the count AFTER the pitch, so we maintain
        # our own running tally and record it before each pitch.
        pa_balls   = 0
        pa_strikes = 0

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
            spin_axis  = breaks.get("spinDirection")
            result     = ev.get("details", {}).get("description", "")

            # Record the pre-pitch count (before this pitch's outcome)
            balls   = pa_balls
            strikes = pa_strikes

            # Advance running count using the post-pitch values from the API
            after_balls   = ev.get("count", {}).get("balls")
            after_strikes = ev.get("count", {}).get("strikes")
            if after_balls is not None and after_strikes is not None:
                pa_balls   = after_balls
                pa_strikes = after_strikes

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
                "spin_axis":  round(spin_axis) if spin_axis is not None else None,
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

# Request all game types at once — no user selection needed
game_type = "R,S,F,D,L,W"

league = st.sidebar.radio(
    "League",
    options=["MLB", "AAA", "FSL"],
    horizontal=True,
)
sport_id = {"MLB": 1, "AAA": 11, "FSL": 14}[league]

if st.sidebar.button("↻ Refresh"):
    st.cache_data.clear()
    # Clear prefetch state so background threads re-fire after refresh
    for key in list(st.session_state.keys()):
        if key.startswith("prefetch_"):
            del st.session_state[key]

# ----------------------------
# Load games for selected date
# ----------------------------
date_str = selected_date.strftime("%Y-%m-%d")
is_today = (selected_date == et_today)

try:
    with st.spinner("Loading games..."):
        games = fetch_games(date_str, game_type, sport_id)
except Exception as e:
    st.error(f"Could not load games: {e}")
    st.stop()

date_label = selected_date.strftime("%A, %B %-d, %Y")
st.caption(f"{'Today — ' if is_today else ''}{date_label} — {league}")

if not games:
    st.info(f"No {league} games found for {date_label}. Try a different date.")
    st.stop()

# ----------------------------
# Game selector
# ----------------------------
def get_team_abbr(game, side):
    """Extract team abbreviation with multiple fallback paths."""
    teams = game.get("teams", {})
    side_data = teams.get(side, {})
    abbr = side_data.get("team", {}).get("abbreviation")
    if abbr:
        return abbr
    abbr = side_data.get("abbreviation")
    if abbr:
        return abbr
    name = side_data.get("team", {}).get("name") or side_data.get("name")
    if name:
        name_to_abbr = {
            "Arizona Diamondbacks": "ARI", "Atlanta Braves": "ATL",
            "Baltimore Orioles": "BAL", "Boston Red Sox": "BOS",
            "Chicago Cubs": "CHC", "Chicago White Sox": "CWS",
            "Cincinnati Reds": "CIN", "Cleveland Guardians": "CLE",
            "Colorado Rockies": "COL", "Detroit Tigers": "DET",
            "Houston Astros": "HOU", "Kansas City Royals": "KC",
            "Los Angeles Angels": "LAA", "Los Angeles Dodgers": "LAD",
            "Miami Marlins": "MIA", "Milwaukee Brewers": "MIL",
            "Minnesota Twins": "MIN", "New York Mets": "NYM",
            "New York Yankees": "NYY", "Oakland Athletics": "OAK",
            "Philadelphia Phillies": "PHI", "Pittsburgh Pirates": "PIT",
            "San Diego Padres": "SD", "San Francisco Giants": "SF",
            "Seattle Mariners": "SEA", "St. Louis Cardinals": "STL",
            "Tampa Bay Rays": "TB", "Texas Rangers": "TEX",
            "Toronto Blue Jays": "TOR", "Washington Nationals": "WSH",
            "Athletics": "OAK",
        }
        return name_to_abbr.get(name, name[:3].upper())
    return "?"

def game_label(game):
    away = get_team_abbr(game, "away")
    home = get_team_abbr(game, "home")
    status = game_status_label(game)
    return f"{away} @ {home}  —  {status}"

game_options = {game_label(g): g for g in games}

# Default to preferred team by league: Daytona (FSL), Louisville (AAA), Reds (MLB)
if sport_id == 14:
    priority_keywords = ("DAY", "Daytona")
elif sport_id == 11:
    priority_keywords = ("LOU", "Louisville")
else:
    priority_keywords = ("CIN", "Cincinnati")
default_idx = 0
for i, (label, g) in enumerate(game_options.items()):
    away = get_team_abbr(g, "away")
    home = get_team_abbr(g, "home")
    away_name = g.get("teams", {}).get("away", {}).get("team", {}).get("name", "")
    home_name = g.get("teams", {}).get("home", {}).get("team", {}).get("name", "")
    all_names = (away, home, away_name, home_name)
    if any(k in n for k in priority_keywords for n in all_names):
        default_idx = i
        break

# Pre-fetch the default game in the background while user reads the selector
default_game = list(game_options.values())[default_idx]
prefetch_feed_background(default_game["gamePk"])

selected_game_label = st.selectbox(
    "Select Game",
    options=list(game_options.keys()),
    index=default_idx,
)
selected_game = game_options[selected_game_label]
game_pk = selected_game["gamePk"]

# Also pre-fetch the selected game if user changed the selection
prefetch_feed_background(game_pk)

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

def build_and_render_team_section(team_abbr, team_name, pitcher_list, boxscore_stats=None):
    if not pitcher_list:
        return

    pitcher_list.sort(key=lambda p: p.get("first_play_idx", 0))
    if boxscore_stats is None:
        boxscore_stats = {}

    swing_results  = {"Swinging Strike", "Swinging Strike (Blocked)", "Foul", "Foul Tip",
                      "In play, out(s)", "In play, no out", "In play, runs"}
    whiff_results  = {"Swinging Strike", "Swinging Strike (Blocked)", "Foul Tip"}
    strike_results = {"Called Strike", "Swinging Strike", "Swinging Strike (Blocked)",
                      "Foul", "Foul Tip", "In play, out(s)", "In play, no out", "In play, runs"}

    headers = ["Pitcher", "IP", "H", "ER", "BB", "K", "Pitches", "Avg Velo", "Max Velo", "Whiffs", "Strikes", "Balls", "InZone%", "Arsenal"]
    header_row = "".join(f"<th>{h}</th>" for h in headers)

    rows_html = ""
    for p in pitcher_list:
        pitches  = p["pitches"]
        total    = len(pitches)
        velos    = [x["velo"] for x in pitches if x["velo"] is not None]
        avg_velo = fmt(round(sum(velos)/len(velos), 1), " mph") if velos else "—"
        max_velo = fmt(round(max(velos), 1), " mph") if velos else "—"
        whiffs   = sum(1 for x in pitches if x["result"] in whiff_results)
        swings   = sum(1 for x in pitches if x["result"] in swing_results)
        whiff_str = f"{whiffs}/{swings}"
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

        # Boxscore stats from live feed
        bs = boxscore_stats.get(p["id"], {})
        ip = bs.get("inningsPitched", "—")
        h  = bs.get("hits",         "—")
        er = bs.get("earnedRuns",   "—")
        bb = bs.get("baseOnBalls",  "—")
        k  = bs.get("strikeOuts",   "—")

        cells = [
            player_link(p["name"]),
            ip, h, er, bb, k,
            total, avg_velo, max_velo, whiff_str, strikes, balls, in_zone_pct, arsenal
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
    build_and_render_team_section(team_abbr, team_name, team_pitchers, boxscore_stats)

box = feed.get("liveData", {}).get("boxscore", {}).get("teams", {})
away_pitcher_ids = box.get("away", {}).get("pitchers", [])
home_pitcher_ids = box.get("home", {}).get("pitchers", [])

# Build boxscore pitching stats lookup: player_id -> stats dict
boxscore_stats = {}
for side in ("away", "home"):
    players = box.get(side, {}).get("players", {})
    for key, pdata in players.items():
        pid = pdata.get("person", {}).get("id")
        stats = pdata.get("stats", {}).get("pitching", {})
        if pid and stats:
            boxscore_stats[pid] = stats

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
        key=lambda i: i[1].get("first_play_idx", 0)
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

# Pitch type filter + batter handedness
pitch_types = sorted(df["pitch_type"].dropna().unique())

_lg_col1, _lg_col2 = st.columns([3, 1])
with _lg_col1:
    selected_types = st.multiselect(
        "Filter by Pitch Type",
        options=pitch_types,
        default=pitch_types,
    )
with _lg_col2:
    batter_hand = st.radio(
        "Batter",
        options=["Both", "R", "L"],
        horizontal=True,
    )

df = df[df["pitch_type"].isin(selected_types)]
if batter_hand != "Both":
    df = df[df["bat_side"] == batter_hand]

# ----------------------------
# Metrics row
# ----------------------------
total   = len(df)
velos   = df["velo"].dropna()
avg_v   = f"{velos.mean():.1f}" if len(velos) else "—"
max_v   = f"{velos.max():.1f}"  if len(velos) else "—"

swing_r  = {"Swinging Strike", "Swinging Strike (Blocked)", "Foul", "Foul Tip",
            "In play, out(s)", "In play, no out", "In play, runs"}
whiff_r  = {"Swinging Strike", "Swinging Strike (Blocked)", "Foul Tip"}
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
m2.metric("Avg Velo (mph)", avg_v)
m3.metric("Max Velo (mph)", max_v)
m4.metric("Whiffs/Swings", f"{whiffs}/{int(swings)}")
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
            "Whiffs":    f"{int(wh)}/{int(sw)}",
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
# Pitch Usage by Count (pitcher detail)
# ----------------------------
count_df_src = df[
    df["balls"].apply(lambda x: str(x).isdigit()) &
    df["strikes"].apply(lambda x: str(x).isdigit())
].copy()
count_df_src["count"] = count_df_src["balls"].astype(str) + "-" + count_df_src["strikes"].astype(str)

ALL_COUNTS = ["0-0", "1-0", "2-0", "3-0", "0-1", "1-1", "2-1", "3-1", "0-2", "1-2", "2-2", "3-2"]
present_counts = [c for c in ALL_COUNTS if c in count_df_src["count"].values]

if not count_df_src.empty and present_counts:
    _pivot = (
        count_df_src.groupby(["count", "pitch_type"])
        .size()
        .reset_index(name="n")
    )
    _totals = count_df_src.groupby("count").size().reset_index(name="total")
    _pivot  = _pivot.merge(_totals, on="count")
    _pivot["pct"] = (_pivot["n"] / _pivot["total"] * 100).round(1)

    _pitch_order = (
        count_df_src.groupby("pitch_type")
        .size()
        .sort_values(ascending=False)
        .index.tolist()
    )

    _table_rows = []
    for cnt in present_counts:
        sub = _pivot[_pivot["count"] == cnt]
        total_n = int(_totals.loc[_totals["count"] == cnt, "total"].values[0])
        row = {"Count": cnt, "Total": total_n}
        for pt in _pitch_order:
            match = sub[sub["pitch_type"] == pt]
            row[pt] = match["pct"].values[0] if not match.empty else 0.0
        _table_rows.append(row)

    _count_table = pd.DataFrame(_table_rows)

    with st.expander("Pitch Usage by Count", expanded=False):
        st.caption("% of pitches thrown in each count. Red = higher usage, blue = lower usage (per pitch type).")

        _col_ranges = {}
        for pt in _pitch_order:
            col_vals = _count_table[pt].values
            _col_ranges[pt] = (col_vals.min(), col_vals.max())

        def _cell_color(val, col_min, col_max):
            if col_max == col_min:
                return "rgba(120,120,120,0.15)"
            t = (val - col_min) / (col_max - col_min)
            if t >= 0.5:
                opacity = 0.15 + (t - 0.5) * 2 * 0.60
                return f"rgba(210,50,50,{opacity:.2f})"
            else:
                opacity = 0.15 + (0.5 - t) * 2 * 0.60
                return f"rgba(50,100,210,{opacity:.2f})"

        _header_cells = "<th>Count</th><th>Total</th>" + "".join(
            f"<th title='{pitch_name(pt)}'>{pt}</th>" for pt in _pitch_order
        )
        _rows_html = ""
        for _, row in _count_table.iterrows():
            cells = (
                f"<td style='font-weight:600'>{row['Count']}</td>"
                f"<td style='color:#aaa'>{int(row['Total'])}</td>"
            )
            for pt in _pitch_order:
                val = row[pt]
                col_min, col_max = _col_ranges[pt]
                bg = _cell_color(val, col_min, col_max)
                cells += f"<td style='background:{bg};text-align:right'>{val:.1f}%</td>"
            _rows_html += f"<tr>{cells}</tr>"

        _css = """
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
            _css
            + "<table class='count-table'><thead><tr>"
            + _header_cells
            + "</tr></thead><tbody>"
            + _rows_html
            + "</tbody></table>",
            unsafe_allow_html=True,
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

        fig = go.Figure()

        # Add scatter traces first
        for pt in plot_df["pitch_type"].unique():
            sub = plot_df[plot_df["pitch_type"] == pt]
            fig.add_trace(go.Scatter(
                x=sub["pfx_x"], y=sub["pfx_z"],
                mode="markers",
                name=f"{pt} — {pitch_name(pt)}",
                marker=dict(color=pitch_color(pt), size=8, opacity=0.8),
                customdata=sub[["velo", "result", "batter", "balls", "strikes", "half", "inning", "outs"]],
                hovertemplate=(
                    "<b>%{fullData.name}</b><br>"
                    "Batter: %{customdata[2]}<br>"
                    "Count: %{customdata[3]}-%{customdata[4]}<br>"
                    "%{customdata[5]} %{customdata[6]}, %{customdata[7]} out<br>"
                    "Velo: %{customdata[0]} mph<br>"
                    "Result: %{customdata[1]}<extra></extra>"
                ),
            ))

        # Add release point markers (mean position per pitch type)
        for _, row in release_df.iterrows():
            pt = row["pitch_type"]
            rx_in = row["rel_x"] * 12
            rz_in = row["rel_z"] * 12
            fig.add_trace(go.Scatter(
                x=[rx_in], y=[rz_in],
                mode="markers",
                marker=dict(
                    symbol="x",
                    size=14,
                    color="white",
                    line=dict(width=2, color="white"),
                ),
                name=pt + " Release",
                showlegend=False,
                hovertemplate=(
                    "<b>" + pt + " Release Point</b><br>"
                    "Horiz: " + str(round(row["rel_x"], 2)) + " ft<br>"
                    "Height: " + str(round(row["rel_z"], 2)) + " ft<extra></extra>"
                ),
            ))

        # Reorder: release markers beneath scatter points
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
        fig2 = go.Figure()
        for pt in loc_df["pitch_type"].unique():
            sub2 = loc_df[loc_df["pitch_type"] == pt]
            fig2.add_trace(go.Scatter(
                x=sub2["p_x"], y=sub2["p_z"],
                mode="markers",
                name=f"{pt} — {pitch_name(pt)}",
                marker=dict(color=pitch_color(pt), size=8, opacity=0.8),
                customdata=sub2[["velo", "result", "batter", "balls", "strikes", "half", "inning", "outs"]],
                hovertemplate=(
                    "<b>%{fullData.name}</b><br>"
                    "Batter: %{customdata[2]}<br>"
                    "Count: %{customdata[3]}-%{customdata[4]}<br>"
                    "%{customdata[5]} %{customdata[6]}, %{customdata[7]} out<br>"
                    "Velo: %{customdata[0]} mph<br>"
                    "Result: %{customdata[1]}<extra></extra>"
                ),
            ))
        # Strike zone
        fig2.add_shape(type="rect", x0=-0.83, x1=0.83, y0=1.5, y1=3.5,
                       line=dict(color="white", width=2))
        fig2.update_xaxes(title="Horizontal (ft)", range=[2, -2], constrain="domain")
        fig2.update_yaxes(title="Height (ft)", range=[0, 6], scaleanchor="x", scaleratio=1)
        fig2.update_layout(height=520, legend=dict(orientation="h", y=-0.15))
        st.plotly_chart(fig2, use_container_width=True)
    else:
        st.info("No location data available yet.")

# ----------------------------
# Velocity by pitch sequence
# ----------------------------
if not df.empty and "velo" in df.columns:
    seq_df = df.reset_index(drop=True).copy()
    seq_df["pitch_num"] = seq_df.index + 1

    velo_fig = go.Figure()

    for pt in seq_df["pitch_type"].unique():
        sub = seq_df[seq_df["pitch_type"] == pt]
        velo_fig.add_trace(go.Scatter(
            x=sub["pitch_num"],
            y=sub["velo"],
            mode="markers",
            name=f"{pt} — {pitch_name(pt)}",
            marker=dict(color=pitch_color(pt), size=8, opacity=0.85),
            customdata=sub[["result", "batter", "balls", "strikes", "half", "inning", "outs"]],
            hovertemplate=(
                "<b>%{fullData.name}</b><br>"
                "Pitch #%{x}<br>"
                "Velo: %{y} mph<br>"
                "Batter: %{customdata[1]}<br>"
                "Count: %{customdata[2]}-%{customdata[3]}<br>"
                "%{customdata[4]} %{customdata[5]}, %{customdata[6]} out<br>"
                "Result: %{customdata[0]}<extra></extra>"
            ),
        ))

    # Add inning divider lines and labels
    if "inning" in seq_df.columns and "half" in seq_df.columns:
        seen = set()
        for _, row in seq_df.iterrows():
            key = (row["inning"], row["half"])
            if key not in seen:
                seen.add(key)
                pnum = row["pitch_num"]
                if pnum > 1:
                    velo_fig.add_vline(
                        x=pnum - 0.5,
                        line=dict(color="rgba(255,255,255,0.2)", width=1, dash="dot"),
                    )
                label = row["half"] + " " + str(row["inning"])
                velo_fig.add_annotation(
                    x=pnum,
                    y=1.02,
                    xref="x",
                    yref="paper",
                    text=label,
                    showarrow=False,
                    font=dict(size=10, color="rgba(255,255,255,0.5)"),
                    xanchor="left",
                )

    velo_fig.update_xaxes(title="Pitch #", showgrid=False)
    velo_fig.update_yaxes(title="Velocity (mph)", showgrid=True,
                          gridcolor="rgba(255,255,255,0.08)")
    velo_fig.update_layout(
        height=350,
        legend=dict(orientation="h", y=-0.2),
        margin=dict(t=30),
    )
    st.markdown("### Velocity by Pitch Sequence")
    st.plotly_chart(velo_fig, use_container_width=True)

# ----------------------------
# Spin Rate & Axis
# ----------------------------
spin_df = df.dropna(subset=["spin_rate", "spin_axis"])

if not spin_df.empty:
    st.markdown("### Spin Rate & Axis")

    spin_fig = go.Figure()

    for pt in sorted(spin_df["pitch_type"].dropna().unique()):
        sub = spin_df[spin_df["pitch_type"] == pt]
        spin_fig.add_trace(go.Scatter(
            x=sub["spin_axis"],
            y=sub["spin_rate"],
            mode="markers",
            name=f"{pt} — {pitch_name(pt)}",
            marker=dict(color=pitch_color(pt), size=8, opacity=0.75),
            customdata=sub[["velo", "result", "batter"]],
            hovertemplate=(
                "<b>%{fullData.name}</b><br>"
                "Spin Rate: %{y} rpm<br>"
                "Spin Axis: %{x}°<br>"
                "Velo: %{customdata[0]} mph<br>"
                "Batter: %{customdata[2]}<br>"
                "Result: %{customdata[1]}<extra></extra>"
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
