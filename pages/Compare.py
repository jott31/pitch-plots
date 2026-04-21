import requests
import unicodedata
from datetime import datetime, timezone, date

import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from pybaseball import statcast_pitcher, pitching_stats
from pybaseball.playerid_lookup import get_lookup_table

st.set_page_config(page_title="Pitcher Comparison", page_icon="⚾", layout="wide")

# ──────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────
BASE      = "https://statsapi.mlb.com/api/v1"
BASE_LIVE = "https://statsapi.mlb.com/api/v1.1"

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
    "SV": "#376748",
    "CS": "#274BFC",
    "PO": "#472C30",
    "UN": "#9C8975",
}

PITCH_NAMES = {
    "FF": "4-Seam", "FA": "4-Seam",
    "SI": "Sinker",  "FT": "Sinker",
    "FC": "Cutter",
    "SL": "Slider",  "ST": "Sweeper",
    "CU": "Curve",   "KC": "Knuckle-Curve",
    "CH": "Changeup",
    "FS": "Splitter", "FO": "Forkball",
    "KN": "Knuckleball",
    "EP": "Eephus",
    "SV": "Slurve",
    "CS": "Slow Curve",
    "PO": "Pitch Out",
    "UN": "Unknown",
}

# Title Case  -> live feed (MLB Stats API)
# snake_case  -> Statcast / pybaseball season data
SWING_R  = {
    "Swinging Strike", "Swinging Strike (Blocked)", "Foul", "Foul Tip",
    "In play, out(s)", "In play, no out", "In play, runs",
    "swinging_strike", "swinging_strike_blocked", "foul", "foul_tip",
    "hit_into_play", "foul_bunt", "missed_bunt",
}
WHIFF_R  = {
    "Swinging Strike", "Swinging Strike (Blocked)", "Foul Tip",
    "swinging_strike", "swinging_strike_blocked", "foul_tip",
}
STRIKE_R = {
    "Called Strike", "Swinging Strike", "Swinging Strike (Blocked)",
    "Foul", "Foul Tip", "In play, out(s)", "In play, no out", "In play, runs",
    "called_strike", "swinging_strike", "swinging_strike_blocked",
    "foul", "foul_tip", "hit_into_play", "foul_bunt", "missed_bunt",
}
BALL_R = {
    "ball", "blocked_ball", "pitchout",
    "Ball", "Ball In Dirt",
}

def pname(code):
    return PITCH_NAMES.get(code, code or "?")

def pcolor(code):
    return PITCH_COLORS.get(code, "#9C8975")

def strip_accents(text):
    return unicodedata.normalize("NFD", str(text)).encode("ascii", "ignore").decode("ascii")

# ──────────────────────────────────────────────
# Cached helpers
# ──────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def load_lookup_table():
    return get_lookup_table()

@st.cache_data(show_spinner=False)
def get_statcast(start_date, end_date, playerid):
    return statcast_pitcher(start_dt=start_date, end_dt=end_date, player_id=playerid)

@st.cache_data(show_spinner=False)
def get_fg_stats(season):
    try:
        stats = pitching_stats(season, qual=0)
        return stats if stats is not None else pd.DataFrame()
    except Exception:
        return pd.DataFrame()

@st.cache_data(ttl=60, show_spinner=False)
def fetch_games(date_str, sport_id=1):
    url = (f"{BASE}/schedule?sportId={sport_id}&date={date_str}"
           "&gameType=R,S,F,D,L,W"
           "&hydrate=teams,linescore,decisions,pitchers")
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    games = []
    for d in r.json().get("dates", []):
        for g in d.get("games", []):
            games.append(g)
    return games

@st.cache_data(ttl=30, show_spinner=False)
def fetch_live_feed(game_pk):
    url = f"{BASE_LIVE}/game/{game_pk}/feed/live"
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    return r.json()

def get_et_today():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York")).date()
    except ImportError:
        from datetime import timedelta
        return (datetime.now(timezone.utc) - timedelta(hours=5)).date()

# ──────────────────────────────────────────────
# Player lookup builder (runs once)
# ──────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def build_player_index(lookup_table):
    named_id = {}
    norm_to_pretty = {}
    valid = lookup_table[
        lookup_table["key_mlbam"].notna() & (lookup_table["key_mlbam"] != "")
    ].drop_duplicates(subset=["key_mlbam"]).copy()
    for _, row in valid.iterrows():
        first = str(row["name_first"]).title()
        last  = str(row["name_last"]).title()
        fy = int(row["mlb_played_first"]) if pd.notna(row.get("mlb_played_first")) else None
        ly = int(row["mlb_played_last"])  if pd.notna(row.get("mlb_played_last"))  else None
        yr = f" ({fy}–{ly})" if fy else ""
        display = f"{first} {last}{yr}"
        norm    = strip_accents(display)
        if norm not in named_id:
            named_id[norm] = {
                "mlbam":      row["key_mlbam"],
                "fangraphs":  row["key_fangraphs"],
                "display_name": f"{first} {last}",
            }
            norm_to_pretty[norm] = display
    return named_id, norm_to_pretty

# ──────────────────────────────────────────────
# Live feed pitch extractor
# ──────────────────────────────────────────────
def extract_pitcher_pitches(feed, target_pid=None):
    """
    Extract pitches from a live feed.
    If target_pid is given, return only that pitcher's pitches as a list.
    Otherwise return dict pid -> {name, team, pitches}.
    """
    away_abbr = feed.get("gameData", {}).get("teams", {}).get("away", {}).get("abbreviation", "?")
    home_abbr = feed.get("gameData", {}).get("teams", {}).get("home", {}).get("abbreviation", "?")
    box       = feed.get("liveData", {}).get("boxscore", {}).get("teams", {})
    away_ids  = set(box.get("away", {}).get("pitchers", []))
    home_ids  = set(box.get("home", {}).get("pitchers", []))

    pitcher_map = {}
    for play in feed.get("liveData", {}).get("plays", {}).get("allPlays", []):
        pitcher = play.get("matchup", {}).get("pitcher")
        if not pitcher:
            continue
        pid   = pitcher["id"]
        pname_str = pitcher.get("fullName", "Unknown")
        if pid not in pitcher_map:
            if pid in away_ids:
                team = away_abbr
            elif pid in home_ids:
                team = home_abbr
            else:
                half = play.get("about", {}).get("halfInning", "")
                team = home_abbr if half == "top" else away_abbr
            pitcher_map[pid] = {"name": pname_str, "team": team, "pitches": []}

        batter   = play.get("matchup", {}).get("batter", {}).get("fullName", "")
        inning   = play.get("about", {}).get("inning", 1)
        half_str = "Top" if play.get("about", {}).get("halfInning") == "top" else "Bot"

        # Track pre-pitch count: ev["count"] is AFTER the pitch, so maintain
        # our own running tally and record it before advancing.
        pa_balls   = 0
        pa_strikes = 0

        for ev in play.get("playEvents", []):
            if not ev.get("isPitch"):
                continue
            pd_    = ev.get("pitchData") or {}
            coords = pd_.get("coordinates", {})
            breaks = pd_.get("breaks", {})
            code   = ev.get("details", {}).get("type", {}).get("code", "XX")
            velo   = pd_.get("startSpeed")
            rh     = breaks.get("breakHorizontal")
            rv     = breaks.get("breakVerticalInduced") or breaks.get("breakVertical")
            pfx_x  = (-rh) if rh is not None else None
            pfx_z  = rv    if rv is not None else None

            # Record pre-pitch count, then advance
            balls   = pa_balls
            strikes = pa_strikes
            after_b = ev.get("count", {}).get("balls")
            after_s = ev.get("count", {}).get("strikes")
            if after_b is not None and after_s is not None:
                pa_balls   = after_b
                pa_strikes = after_s

            pitcher_map[pid]["pitches"].append({
                "pitch_type": code,
                "velo":       round(velo, 1)  if velo  is not None else None,
                "pfx_x":      round(pfx_x, 2) if pfx_x is not None else None,
                "pfx_z":      round(pfx_z, 2) if pfx_z is not None else None,
                "p_x":        round(coords.get("pX", 0) or 0, 2),
                "p_z":        round(coords.get("pZ", 0) or 0, 2),
                "spin_rate":  round(breaks.get("spinRate")) if breaks.get("spinRate") else None,
                "spin_axis":  round(breaks.get("spinDirection")) if breaks.get("spinDirection") else None,
                "result":     ev.get("details", {}).get("description", ""),
                "balls":      balls,
                "strikes":    strikes,
                "batter":     batter,
                "inning":     inning,
                "half":       half_str,
            })

    if target_pid is not None:
        return pitcher_map.get(target_pid, {}).get("pitches", [])
    return pitcher_map

# ──────────────────────────────────────────────
# Data builders
# ──────────────────────────────────────────────
def statcast_to_pitch_list(df):
    """Convert a statcast DataFrame row-by-row into the same dict format as live feed pitches."""
    rows = []
    for _, r in df.iterrows():
        rh = r.get("pfx_x")   # already in feet from pybaseball
        rv = r.get("pfx_z")
        rows.append({
            "pitch_type": r.get("pitch_type", "XX"),
            "velo":       round(r["release_speed"], 1) if pd.notna(r.get("release_speed")) else None,
            "pfx_x":      round(rh * 12, 2) if pd.notna(rh) else None,
            "pfx_z":      round(rv * 12, 2) if pd.notna(rv) else None,
            "p_x":        round(r["plate_x"], 2) if pd.notna(r.get("plate_x")) else None,
            "p_z":        round(r["plate_z"], 2) if pd.notna(r.get("plate_z")) else None,
            "spin_rate":  round(r["release_spin_rate"]) if pd.notna(r.get("release_spin_rate")) else None,
            "spin_axis":  round(r["spin_axis"]) if pd.notna(r.get("spin_axis")) else None,
            "result":     r.get("description", ""),
            "balls":      r.get("balls", "?"),
            "strikes":    r.get("strikes", "?"),
            "batter":     r.get("batter_name", ""),
            "bat_side":   r.get("stand", ""),
            "inning":     r.get("inning", "?"),
            "half":       "Top" if r.get("inning_topbot") == "Top" else "Bot",
        })
    return rows

def get_fg_row(player_name, season):
    fg = get_fg_stats(season)
    if fg.empty:
        return None
    match = fg[fg["Name"].apply(lambda n: strip_accents(n).lower()) == strip_accents(player_name).lower()]
    return match.iloc[0] if not match.empty else None

# ──────────────────────────────────────────────
# Pitch metric aggregator
# ──────────────────────────────────────────────
def aggregate_pitches(pitches):
    """Return per-pitch-type metrics dict and overall metrics dict."""
    if not pitches:
        return pd.DataFrame(), {}

    df = pd.DataFrame(pitches)
    # Ensure required columns exist with safe defaults
    for col in ("pitch_type", "velo", "result", "p_x", "p_z"):
        if col not in df.columns:
            df[col] = None

    # Drop rows with no pitch type
    df = df[df["pitch_type"].notna() & (df["pitch_type"] != "")]
    if df.empty:
        return pd.DataFrame(), {}

    total = len(df)
    velos = df["velo"].dropna()

    def in_zone_count(frame):
        """Count pitches in strike zone, safely ignoring NaN coords."""
        pz = pd.to_numeric(frame["p_z"], errors="coerce")
        px = pd.to_numeric(frame["p_x"], errors="coerce")
        mask = pz.between(1.5, 3.5) & px.between(-0.83, 0.83)
        return int(mask.sum())

    overall = {
        "total":    total,
        "avg_velo": round(velos.mean(), 1) if len(velos) else None,
        "max_velo": round(velos.max(),  1) if len(velos) else None,
        "whiffs":   int(df["result"].isin(WHIFF_R).sum()),
        "swings":   int(df["result"].isin(SWING_R).sum()),
        "strikes":  int(df["result"].isin(STRIKE_R).sum()),
        "balls":    int(df["result"].isin(BALL_R).sum()),
        "in_zone":  in_zone_count(df),
    }

    rows = []
    # sorted() with key avoids TypeError when mixing str/None
    pitch_types = sorted(df["pitch_type"].dropna().unique(), key=lambda x: str(x))
    for pt in pitch_types:
        sub  = df[df["pitch_type"] == pt]
        n    = len(sub)
        v    = sub["velo"].dropna()
        sw   = int(sub["result"].isin(SWING_R).sum())
        wh   = int(sub["result"].isin(WHIFF_R).sum())
        iz   = in_zone_count(sub)
        rows.append({
            "pitch_type": pt,
            "Count":      n,
            "Usage%":     round(n / total * 100, 1),
            "Avg Velo":   round(v.mean(), 1) if len(v) else None,
            "Max Velo":   round(v.max(),  1) if len(v) else None,
            "Whiff%":     round(wh / sw * 100, 1) if sw else 0.0,
            "InZone%":    round(iz / n  * 100, 1) if n  else 0.0,
        })
    return pd.DataFrame(rows), overall

# ──────────────────────────────────────────────
# Shared plot helpers
# ──────────────────────────────────────────────
def movement_fig(pitches, title="Movement"):
    df = pd.DataFrame(pitches).dropna(subset=["pfx_x", "pfx_z"])
    fig = go.Figure()
    if df.empty:
        fig.update_layout(title=title, height=380)
        return fig
    for pt in df["pitch_type"].unique():
        sub = df[df["pitch_type"] == pt]
        fig.add_trace(go.Scatter(
            x=sub["pfx_x"], y=sub["pfx_z"],
            mode="markers",
            name=f"{pt}",
            marker=dict(color=pcolor(pt), size=7, opacity=0.75),
            hovertemplate=(
                f"<b>{pt} — {pname(pt)}</b><br>"
                "HB: %{x:.1f} in<br>VB: %{y:.1f} in<extra></extra>"
            ),
        ))
    # centroids
    for pt in df["pitch_type"].unique():
        sub = df[df["pitch_type"] == pt][["pfx_x", "pfx_z"]].dropna()
        if sub.empty:
            continue
        fig.add_trace(go.Scatter(
            x=[sub["pfx_x"].mean()], y=[sub["pfx_z"].mean()],
            mode="markers+text",
            marker=dict(symbol="x", size=13, color="white", line=dict(width=2, color="white")),
            text=[pt], textposition="top center",
            textfont=dict(size=10, color="white"),
            showlegend=False,
            hovertemplate=(
                f"<b>{pt} centroid</b><br>"
                "Avg HB: %{x:.1f} in<br>Avg VB: %{y:.1f} in<extra></extra>"
            ),
        ))
    fig.add_hline(y=0, line_color="white", line_width=1)
    fig.add_vline(x=0, line_color="white", line_width=1)
    fig.update_xaxes(title="Horizontal Break (in)", range=[25, -25])
    fig.update_yaxes(title="Vertical Break (in)",   range=[-25, 25])
    fig.update_layout(title=title, height=380,
                      legend=dict(orientation="h", y=-0.22),
                      margin=dict(t=40, b=10))
    return fig

def location_fig(pitches, title="Location"):
    df = pd.DataFrame(pitches).dropna(subset=["p_x", "p_z"])
    fig = go.Figure()
    if df.empty:
        fig.update_layout(title=title, height=420)
        return fig
    for pt in df["pitch_type"].unique():
        sub = df[df["pitch_type"] == pt]
        fig.add_trace(go.Scatter(
            x=sub["p_x"], y=sub["p_z"],
            mode="markers", name=f"{pt}",
            marker=dict(color=pcolor(pt), size=7, opacity=0.7),
            hovertemplate=(
                f"<b>{pt} — {pname(pt)}</b><br>"
                "Horiz: %{x:.2f} ft<br>Height: %{y:.2f} ft<extra></extra>"
            ),
        ))
    fig.add_shape(type="rect", x0=-0.83, x1=0.83, y0=1.5, y1=3.5,
                  line=dict(color="white", width=2))
    fig.update_xaxes(title="Horizontal (ft)", range=[2, -2], constrain="domain")
    fig.update_yaxes(title="Height (ft)", range=[0, 6], scaleanchor="x", scaleratio=1)
    fig.update_layout(title=title, height=460,
                      legend=dict(orientation="h", y=-0.18),
                      margin=dict(t=40, b=10))
    return fig

def velo_seq_fig(pitches, title="Velocity Sequence"):
    df = pd.DataFrame(pitches).reset_index(drop=True)
    df["pitch_num"] = df.index + 1
    fig = go.Figure()
    if df.empty or "velo" not in df.columns:
        fig.update_layout(title=title, height=300)
        return fig
    for pt in df["pitch_type"].unique():
        sub = df[df["pitch_type"] == pt]
        fig.add_trace(go.Scatter(
            x=sub["pitch_num"], y=sub["velo"],
            mode="markers", name=pt,
            marker=dict(color=pcolor(pt), size=7, opacity=0.85),
            hovertemplate=(
                f"<b>{pt}</b><br>Pitch #%{{x}}<br>Velo: %{{y}} mph<extra></extra>"
            ),
        ))
    fig.update_xaxes(title="Pitch #", showgrid=False)
    fig.update_yaxes(title="Velocity (mph)", showgrid=True,
                     gridcolor="rgba(255,255,255,0.08)")
    fig.update_layout(title=title, height=300,
                      legend=dict(orientation="h", y=-0.25),
                      margin=dict(t=40, b=10))
    return fig

def spin_fig(pitches, title="Spin Rate & Axis"):
    df = pd.DataFrame(pitches).dropna(subset=["spin_rate", "spin_axis"])
    fig = go.Figure()
    if df.empty:
        fig.update_layout(title=title, height=340)
        return fig
    for pt in sorted(df["pitch_type"].unique()):
        sub = df[df["pitch_type"] == pt]
        fig.add_trace(go.Scatter(
            x=sub["spin_axis"], y=sub["spin_rate"],
            mode="markers", name=pt,
            marker=dict(color=pcolor(pt), size=7, opacity=0.75),
            hovertemplate=(
                f"<b>{pt}</b><br>Spin: %{{y}} rpm<br>Axis: %{{x}}°<extra></extra>"
            ),
        ))
    fig.update_xaxes(title="Spin Axis (°)", range=[0, 360],
                     tickvals=[0, 90, 180, 270, 360],
                     ticktext=["0°", "90°", "180°", "270°", "360°"])
    fig.update_yaxes(title="Spin Rate (rpm)", showgrid=True,
                     gridcolor="rgba(255,255,255,0.08)")
    fig.update_layout(title=title, height=340,
                      legend=dict(orientation="h", y=-0.25),
                      margin=dict(t=40, b=10))
    return fig

# ──────────────────────────────────────────────
# Game vs Season delta heatmap table
# ──────────────────────────────────────────────
# Stat polarity: True = higher is BETTER for pitcher (red), False = lower is BETTER (red)
STAT_POLARITY = {
    "Avg Velo":  True,   # harder = better
    "Max Velo":  True,
    "Whiff%":    True,   # more whiffs = better
    "InZone%":   True,   # more zone strikes = better
    "Usage%":    None,   # informational only, no colour
    "Count":     None,
}


def _delta_color(delta, polarity, magnitude):
    """
    Return an rgba CSS string.
    polarity True  -> positive delta = good for pitcher -> red
    polarity False -> positive delta = bad  for pitcher -> blue
    polarity None  -> neutral grey
    Opacity scales 0.15-0.75 with magnitude.
    """
    if polarity is None or delta is None or magnitude == 0:
        return "rgba(120,120,120,0.10)"
    opacity = min(0.75, max(0.15, magnitude / 10.0 * 0.6 + 0.15))
    is_good = (delta > 0 and polarity) or (delta < 0 and not polarity)
    if is_good:
        return f"rgba(220, 50, 50, {opacity:.2f})"
    else:
        return f"rgba(50, 100, 220, {opacity:.2f})"


def render_comparison_table(pitches_left, pitches_right, label_left="Left", label_right="Right"):
    """
    Render a colour-coded HTML comparison table for any two pitch datasets.
    Left column cells are coloured relative to right (baseline).
    Red = left is better for pitcher, Blue = left is worse.
    Intensity scales with magnitude of difference.
    """
    _, ov_left   = aggregate_pitches(pitches_left)
    _, ov_right  = aggregate_pitches(pitches_right)
    bd_left,  _  = aggregate_pitches(pitches_left)
    bd_right, _  = aggregate_pitches(pitches_right)

    if bd_left.empty or bd_right.empty:
        st.info("Not enough data in one or both datasets to render comparison table.")
        return

    def fmt_val(val, key):
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return "\u2014"
        if key in ("Whiff%", "InZone%", "Usage%"):
            return f"{val:.1f}%"
        if key in ("Avg Velo", "Max Velo"):
            return f"{val:.1f}"
        return str(val)

    def whiff_pct(ov):
        return round(ov["whiffs"] / ov["swings"] * 100, 1) if ov.get("swings") else None

    def zone_pct(ov):
        return round(ov["in_zone"] / ov["total"] * 100, 1) if ov.get("total") else None

    overall_left  = {
        "Avg Velo": ov_left.get("avg_velo"),  "Max Velo": ov_left.get("max_velo"),
        "Whiff%": whiff_pct(ov_left),  "InZone%": zone_pct(ov_left),
    }
    overall_right = {
        "Avg Velo": ov_right.get("avg_velo"), "Max Velo": ov_right.get("max_velo"),
        "Whiff%": whiff_pct(ov_right), "InZone%": zone_pct(ov_right),
    }

    merged = pd.merge(
        bd_left.rename(columns=lambda c:  c + "_g" if c != "pitch_type" else c),
        bd_right.rename(columns=lambda c: c + "_s" if c != "pitch_type" else c),
        on="pitch_type", how="outer",
    ).fillna(0)

    stat_cols = ["Avg Velo", "Max Velo", "Whiff%", "InZone%", "Usage%"]
    ll = label_left
    lr = label_right

    st.markdown(
        f"<div style='font-size:12px;color:#aaa;margin-bottom:6px'>"
        f"<span style='color:rgba(220,50,50,0.75)'>&#9632;</span> <b>{ll}</b> better &nbsp;&nbsp;"
        f"<span style='color:rgba(50,100,220,0.75)'>&#9632;</span> <b>{ll}</b> worse &nbsp;&nbsp;"
        f"Intensity = size of difference &nbsp;&nbsp;(baseline: <b>{lr}</b>)"
        f"</div>",
        unsafe_allow_html=True,
    )

    col_headers = (
        ["Pitch",
         "Count<br><small>" + ll + "</small>",
         "Count<br><small>" + lr + "</small>"]
        + [s + "<br><small>" + ll + "</small>" for s in stat_cols]
        + [s + "<br><small>" + lr + "</small>" for s in stat_cols]
    )
    header_html = "".join(f"<th>{h}</th>" for h in col_headers)

    def build_row(pt_label, count_g, count_s, left_vals, right_vals, bold=False):
        bopen  = "<b>" if bold else ""
        bclose = "</b>" if bold else ""
        cells  = [
            f"<td>{bopen}{pt_label}{bclose}</td>",
            f"<td style='text-align:right'>{bopen}{count_g}{bclose}</td>",
            f"<td style='text-align:right;color:#aaa'>{bopen}{count_s}{bclose}</td>",
        ]
        for stat in stat_cols:
            l_f = left_vals.get(stat)
            r_f = right_vals.get(stat)
            try:
                l_f = float(l_f) if l_f is not None else None
                r_f = float(r_f) if r_f is not None else None
            except (TypeError, ValueError):
                l_f = r_f = None
            if l_f is not None and r_f is not None and r_f != 0:
                delta     = l_f - r_f
                mag       = abs(delta)
                color     = _delta_color(delta, STAT_POLARITY.get(stat), mag)
                delta_txt = ("+" if delta > 0 else "") + f"{delta:.1f}"
                tooltip   = f"title='vs {lr}: {delta_txt}'"
            else:
                color   = "rgba(120,120,120,0.10)"
                tooltip = ""
            display = fmt_val(l_f, stat)
            cells.append(
                f"<td style='background:{color};text-align:right' {tooltip}>"
                f"{bopen}{display}{bclose}</td>"
            )
        for stat in stat_cols:
            r_f = right_vals.get(stat)
            try:
                r_f = float(r_f) if r_f is not None else None
            except (TypeError, ValueError):
                r_f = None
            display = fmt_val(r_f, stat)
            cells.append(f"<td style='text-align:right;color:#aaa'>{bopen}{display}{bclose}</td>")
        return "<tr>" + "".join(cells) + "</tr>"

    rows_html = ""
    for _, row in merged.iterrows():
        pt      = row["pitch_type"]
        count_g = int(row.get("Count_g", 0))
        count_s = int(row.get("Count_s", 0))

        def _rv(suffix):
            return {s: (row.get(s + suffix) or None) for s in stat_cols}

        rows_html += build_row(pt, count_g, count_s, _rv("_g"), _rv("_s"))

    # ALL summary row
    rows_html += (
        "<tr style='border-top:2px solid #555'>"
        + build_row(
            "ALL",
            ov_left.get("total", "\u2014"),
            ov_right.get("total", "\u2014"),
            overall_left,
            overall_right,
            bold=True,
        )[4:-5]
        + "</tr>"
    )

    css = """
    <style>
      .delta-table {
        width:100%; border-collapse:collapse; font-size:13px; margin-bottom:20px;
      }
      .delta-table th {
        text-align:center; padding:5px 8px; border-bottom:2px solid #444;
        font-family:monospace; font-size:11px; color:#aaa; text-transform:uppercase;
        line-height:1.4;
      }
      .delta-table td { padding:5px 10px; border-bottom:1px solid #1e1e1e; }
    </style>
    """
    table_html = (
        css
        + "<div style='overflow-x:auto'>"
        + "<table class='delta-table'>"
        + "<thead><tr>" + header_html + "</tr></thead>"
        + "<tbody>" + rows_html + "</tbody>"
        + "</table></div>"
    )
    st.markdown(table_html, unsafe_allow_html=True)


# ──────────────────────────────────────────────
# Pitch usage by count helper
# ──────────────────────────────────────────────
def render_count_table(pitches, key_suffix=""):
    """
    Render a Pitch Usage by Count table for a list of pitch dicts.
    Cells are heat-mapped per column: red = higher usage, blue = lower usage.
    """
    if not pitches:
        return

    df = pd.DataFrame(pitches)
    if "balls" not in df.columns or "strikes" not in df.columns:
        return

    df = df[
        df["balls"].apply(lambda x: str(x).isdigit()) &
        df["strikes"].apply(lambda x: str(x).isdigit())
    ].copy()
    if df.empty:
        return

    df["count"] = df["balls"].astype(str) + "-" + df["strikes"].astype(str)

    ALL_COUNTS = ["0-0", "1-0", "2-0", "3-0", "0-1", "1-1", "2-1", "3-1", "0-2", "1-2", "2-2", "3-2"]
    present_counts = [c for c in ALL_COUNTS if c in df["count"].values]
    if not present_counts:
        return

    pivot   = df.groupby(["count", "pitch_type"]).size().reset_index(name="n")
    totals  = df.groupby("count").size().reset_index(name="total")
    pivot   = pivot.merge(totals, on="count")
    pivot["pct"] = (pivot["n"] / pivot["total"] * 100).round(1)

    pitch_order = (
        df.groupby("pitch_type")
        .size()
        .sort_values(ascending=False)
        .index.tolist()
    )

    rows = []
    for cnt in present_counts:
        sub     = pivot[pivot["count"] == cnt]
        total_n = int(totals.loc[totals["count"] == cnt, "total"].values[0])
        row     = {"Count": cnt, "Total": total_n}
        for pt in pitch_order:
            match  = sub[sub["pitch_type"] == pt]
            row[pt] = match["pct"].values[0] if not match.empty else 0.0
        rows.append(row)

    count_table = pd.DataFrame(rows)

    # Per-column min/max for independent heat-mapping
    col_ranges = {}
    for pt in pitch_order:
        col_vals = count_table[pt].values
        col_ranges[pt] = (col_vals.min(), col_vals.max())

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

    header_cells = "<th>Count</th><th>Total</th>" + "".join(
        f"<th title='{pname(pt)}'>{pt}</th>" for pt in pitch_order
    )
    rows_html = ""
    for _, row in count_table.iterrows():
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

    with st.expander("Pitch Usage by Count", expanded=False):
        st.caption("% of pitches thrown in each count. Red = higher usage, blue = lower usage (per pitch type).")
        st.markdown(
            css
            + "<table class='count-table'><thead><tr>"
            + header_cells
            + "</tr></thead><tbody>"
            + rows_html
            + "</tbody></table>",
            unsafe_allow_html=True,
        )


# ──────────────────────────────────────────────
# Render a full pitcher panel (one side)
# ──────────────────────────────────────────────
def render_panel(label, pitches, player_name, season, fg_row, source_tag):
    """Render all comparison metrics + charts for one pitcher into the current column."""

    # ── FanGraphs season summary card
    st.markdown(f"#### 📋 {season} Season Stats")
    if fg_row is not None:
        cols = st.columns(3)
        cols[0].metric("ERA",  f"{fg_row['ERA']:.2f}")
        cols[1].metric("FIP",  f"{fg_row['FIP']:.2f}")
        cols[2].metric("IP",   f"{fg_row['IP']:.1f}")
        cols2 = st.columns(2)
        cols2[0].metric("K%",  f"{fg_row['K%']*100:.1f}%")
        cols2[1].metric("BB%", f"{fg_row['BB%']*100:.1f}%")
    else:
        st.caption("No FanGraphs data found.")

    st.markdown(f"#### ⚾ {source_tag} Pitch Data")
    if not pitches:
        st.info("No pitch data available.")
        return

    # ── Batter handedness filter
    batter_hand = st.radio(
        "Batter",
        options=["Both", "R", "L"],
        horizontal=True,
        key=f"batter_hand_{label.replace(' ', '_')}",
    )
    if batter_hand != "Both":
        pitches = [p for p in pitches if str(p.get("bat_side", p.get("stand", ""))) == batter_hand]

    if not pitches:
        st.info(f"No pitches found vs {'RHH' if batter_hand == 'R' else 'LHH'}.")
        return

    breakdown_df, overall = aggregate_pitches(pitches)
    wh  = overall.get("whiffs", 0)
    sw  = overall.get("swings", 0)
    iz  = overall.get("in_zone", 0)
    tot = overall.get("total",  0)
    q1, q2, q3, q4 = st.columns(4)
    q1.metric("Pitches",   tot)
    q2.metric("Avg Velo",  f"{overall['avg_velo']} mph" if overall["avg_velo"] else "—")
    q3.metric("Whiff%",    f"{round(wh/sw*100,1)}%" if sw else "—")
    q4.metric("Zone%",     f"{round(iz/tot*100,1)}%" if tot else "—")

    # ── Per-pitch breakdown table
    if not breakdown_df.empty:
        st.dataframe(
            breakdown_df.rename(columns={"pitch_type": "Pitch"}),
            column_config={
                "Usage%":    st.column_config.NumberColumn(format="%.1f%%"),
                "Avg Velo":  st.column_config.NumberColumn(format="%.1f mph"),
                "Max Velo":  st.column_config.NumberColumn(format="%.1f mph"),
                "Whiff%":    st.column_config.NumberColumn(format="%.1f%%"),
                "InZone%":   st.column_config.NumberColumn(format="%.1f%%"),
            },
            use_container_width=True,
            hide_index=True,
        )

    # ── Pitch usage by count
    render_count_table(pitches, key_suffix=label.replace(" ", "_"))

    # ── Charts
    st.plotly_chart(movement_fig(pitches, f"Movement — {label}"),
                    use_container_width=True, key=f"mv_{label}")
    st.plotly_chart(location_fig(pitches, f"Location — {label}"),
                    use_container_width=True, key=f"loc_{label}")
    st.plotly_chart(velo_seq_fig(pitches, f"Velo Sequence — {label}"),
                    use_container_width=True, key=f"vs_{label}")
    st.plotly_chart(spin_fig(pitches, f"Spin — {label}"),
                    use_container_width=True, key=f"sp_{label}")


# ──────────────────────────────────────────────
# Game selector widget (reusable)
# ──────────────────────────────────────────────
def get_team_abbr(game, side):
    teams    = game.get("teams", {})
    side_data = teams.get(side, {})
    abbr = side_data.get("team", {}).get("abbreviation") or side_data.get("abbreviation")
    if abbr:
        return abbr
    name = side_data.get("team", {}).get("name") or side_data.get("name", "")
    NAME_MAP = {
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
    }
    return NAME_MAP.get(name, name[:3].upper() if name else "?")

def game_label(game):
    away   = get_team_abbr(game, "away")
    home   = get_team_abbr(game, "home")
    state  = game.get("status", {}).get("abstractGameState", "")
    detail = game.get("status", {}).get("detailedState", "")
    if state == "Live":
        ls     = game.get("linescore", {})
        inn    = ls.get("currentInning", "?")
        half   = "▲" if ls.get("isTopInning", True) else "▼"
        ar     = ls.get("teams", {}).get("away", {}).get("runs", "?")
        hr     = ls.get("teams", {}).get("home", {}).get("runs", "?")
        return f"🔴 {away} @ {home}  {ar}–{hr}  {half}{inn}"
    if state == "Final":
        ar = game.get("linescore", {}).get("teams", {}).get("away", {}).get("runs", "")
        hr = game.get("linescore", {}).get("teams", {}).get("home", {}).get("runs", "")
        return f"✅ {away} @ {home}  Final {ar}–{hr}"
    gd = game.get("gameDate", "")
    if gd:
        try:
            from zoneinfo import ZoneInfo
            dt = datetime.fromisoformat(gd.replace("Z", "+00:00"))
            et = dt.astimezone(ZoneInfo("America/New_York"))
            return f"{away} @ {home}  {et.strftime('%-I:%M %p ET')}"
        except Exception:
            pass
    return f"{away} @ {home}  {detail}"

def pitcher_options_from_feed(feed):
    """Return dict label -> (pid, name) for all pitchers with pitches."""
    pm = extract_pitcher_pitches(feed)
    opts = {}
    for pid, p in sorted(pm.items(), key=lambda i: i[1]["name"]):
        if p["pitches"]:
            opts[f"{p['name']} ({p['team']})  {len(p['pitches'])} pitches"] = (pid, p["name"])
    return opts

# ──────────────────────────────────────────────
# Sidebar: player search widget (A or B)
# ──────────────────────────────────────────────
def sidebar_player_search(slot_key, label, named_id, norm_to_pretty):
    """
    Renders search + selectbox in sidebar for one player slot.
    Returns (playerid, player_name) or (None, None).
    """
    st.sidebar.markdown(f"### {label}")
    q = st.sidebar.text_input(
        f"Search {label}",
        key=f"search_{slot_key}",
        placeholder="e.g. Greene, Lodolo",
        label_visibility="collapsed",
    )
    if not q or len(q.strip()) < 2:
        st.sidebar.caption("Type ≥ 2 characters")
        return None, None

    norm_q   = strip_accents(q.strip()).lower()
    matches  = sorted([n for n in named_id if norm_q in n.lower()])
    if not matches:
        st.sidebar.error("No players found")
        return None, None

    st.sidebar.caption(f"{len(matches)} result(s)")
    selected = st.sidebar.selectbox(
        f"Select {label}",
        options=matches,
        format_func=lambda n: norm_to_pretty[n],
        key=f"sel_{slot_key}",
        label_visibility="collapsed",
    )
    if selected is None:
        return None, None

    pid  = named_id[selected]["mlbam"]
    name = named_id[selected]["display_name"]
    return pid, name


# ══════════════════════════════════════════════
# PAGE START
# ══════════════════════════════════════════════
st.title("⚾ Pitcher Comparison")

with st.spinner("Loading player database…"):
    lookup_table = load_lookup_table()

named_id, norm_to_pretty = build_player_index(lookup_table)

# ──────────────────────────────────────────────
# Sidebar: comparison mode
# ──────────────────────────────────────────────
st.sidebar.header("🔀 Comparison Mode")
mode = st.sidebar.radio(
    "Mode",
    ["Player vs Player", "Season vs Season", "Game vs Season"],
    label_visibility="collapsed",
)

et_today = get_et_today()

# ══════════════════════════════════════════════
# MODE 1 — Player vs Player
# ══════════════════════════════════════════════
if mode == "Player vs Player":
    st.sidebar.markdown("---")
    pid_a, name_a = sidebar_player_search("a", "🔵 Player A", named_id, norm_to_pretty)
    st.sidebar.markdown("---")
    pid_b, name_b = sidebar_player_search("b", "🔴 Player B", named_id, norm_to_pretty)

    st.sidebar.markdown("---")
    season = st.sidebar.number_input("Season", min_value=2015, max_value=et_today.year,
                                     value=et_today.year, step=1)

    if not pid_a and not pid_b:
        st.info("👈 Search for two pitchers in the sidebar to start comparing.")
        st.stop()

    # Fetch data for both players
    def load_player(pid, name):
        if pid is None:
            return None, None
        with st.spinner(f"Loading {name}…"):
            try:
                sc = get_statcast(f"{season}-03-20", f"{season}-10-01", pid)
                pitches = statcast_to_pitch_list(sc) if not sc.empty else []
            except Exception:
                pitches = []
            fg_row = get_fg_row(name, season)
        return pitches, fg_row

    pitches_a, fg_a = load_player(pid_a, name_a)
    pitches_b, fg_b = load_player(pid_b, name_b)

    label_a = name_a or "Player A"
    label_b = name_b or "Player B"

    # ── Delta heatmap table (when both players loaded)
    if pitches_a is not None and pitches_b is not None and pitches_a and pitches_b:
        st.markdown("### 📊 Head-to-Head Pitch Comparison")
        render_comparison_table(pitches_a, pitches_b,
                                label_left=label_a, label_right=label_b)
        st.markdown("---")

    # ── Mobile tabs vs desktop columns
    is_mobile = st.checkbox("📱 Mobile layout (tabs)", value=False,
                            help="Enable for narrow screens")

    if is_mobile:
        tab_a, tab_b = st.tabs([f"🔵 {label_a}", f"🔴 {label_b}"])
        with tab_a:
            if pitches_a is not None:
                render_panel(label_a, pitches_a, name_a, season, fg_a, f"{season} Season")
            else:
                st.info("Search for Player A in the sidebar.")
        with tab_b:
            if pitches_b is not None:
                render_panel(label_b, pitches_b, name_b, season, fg_b, f"{season} Season")
            else:
                st.info("Search for Player B in the sidebar.")
    else:
        col_a, col_b = st.columns(2, gap="large")
        with col_a:
            st.markdown(f"## 🔵 {label_a}")
            if pitches_a is not None:
                render_panel(label_a, pitches_a, name_a, season, fg_a, f"{season} Season")
            else:
                st.info("Search for Player A in the sidebar.")
        with col_b:
            st.markdown(f"## 🔴 {label_b}")
            if pitches_b is not None:
                render_panel(label_b, pitches_b, name_b, season, fg_b, f"{season} Season")
            else:
                st.info("Search for Player B in the sidebar.")


# ══════════════════════════════════════════════
# MODE 2 — Season vs Season (same pitcher)
# ══════════════════════════════════════════════
elif mode == "Season vs Season":
    st.sidebar.markdown("---")
    pid, player_name = sidebar_player_search("svs", "🔍 Pitcher", named_id, norm_to_pretty)

    st.sidebar.markdown("---")
    season_a = st.sidebar.number_input("Season A (left)",  min_value=2015,
                                       max_value=et_today.year, value=max(2015, et_today.year - 1), step=1)
    season_b = st.sidebar.number_input("Season B (right)", min_value=2015,
                                       max_value=et_today.year, value=et_today.year, step=1)

    if not pid:
        st.info("👈 Search for a pitcher in the sidebar.")
        st.stop()

    with st.spinner(f"Loading {player_name} — {season_a}…"):
        try:
            sc_a = get_statcast(f"{season_a}-03-20", f"{season_a}-10-01", pid)
            pitches_a = statcast_to_pitch_list(sc_a) if not sc_a.empty else []
        except Exception:
            pitches_a = []
        fg_a = get_fg_row(player_name, season_a)

    with st.spinner(f"Loading {player_name} — {season_b}…"):
        try:
            sc_b = get_statcast(f"{season_b}-03-20", f"{season_b}-10-01", pid)
            pitches_b = statcast_to_pitch_list(sc_b) if not sc_b.empty else []
        except Exception:
            pitches_b = []
        fg_b = get_fg_row(player_name, season_b)

    st.subheader(f"{player_name}  ·  {season_a} vs {season_b}")

    st.markdown("### 📊 Season Comparison")
    render_comparison_table(pitches_a, pitches_b,
                            label_left=str(season_a), label_right=str(season_b))
    st.markdown("---")

    is_mobile = st.checkbox("📱 Mobile layout (tabs)", value=False)

    if is_mobile:
        tab_a, tab_b = st.tabs([f"📅 {season_a}", f"📅 {season_b}"])
        with tab_a:
            render_panel(str(season_a), pitches_a, player_name, season_a, fg_a, f"{season_a} Full Season")
        with tab_b:
            render_panel(str(season_b), pitches_b, player_name, season_b, fg_b, f"{season_b} Full Season")
    else:
        col_a, col_b = st.columns(2, gap="large")
        with col_a:
            st.markdown(f"## 📅 {season_a}")
            render_panel(str(season_a), pitches_a, player_name, season_a, fg_a, f"{season_a} Full Season")
        with col_b:
            st.markdown(f"## 📅 {season_b}")
            render_panel(str(season_b), pitches_b, player_name, season_b, fg_b, f"{season_b} Full Season")


# ══════════════════════════════════════════════
# MODE 3 — Game vs Season
# ══════════════════════════════════════════════
elif mode == "Game vs Season":
    st.sidebar.markdown("---")
    st.sidebar.markdown("#### 🎮 Game Settings")
    sport_id = 1 if st.sidebar.radio("League", ["MLB", "AAA"], horizontal=True) == "MLB" else 11
    game_date = st.sidebar.date_input("Game Date", value=et_today,
                                      max_value=et_today,
                                      min_value=date(2008, 1, 1))

    date_str = game_date.strftime("%Y-%m-%d")

    try:
        with st.spinner("Loading games…"):
            games = fetch_games(date_str, sport_id)
    except Exception as e:
        st.error(f"Could not load games: {e}")
        st.stop()

    if not games:
        st.info(f"No games found for {date_str}.")
        st.stop()

    game_opts = {game_label(g): g for g in games}
    # default to Reds / Louisville
    pri = ("LOU", "Louisville") if sport_id == 11 else ("CIN", "Cincinnati")
    def_idx = 0
    for i, (lbl, g) in enumerate(game_opts.items()):
        names = (get_team_abbr(g, "away"), get_team_abbr(g, "home"),
                 g.get("teams",{}).get("away",{}).get("team",{}).get("name",""),
                 g.get("teams",{}).get("home",{}).get("team",{}).get("name",""))
        if any(k in n for k in pri for n in names):
            def_idx = i
            break

    sel_game_lbl = st.sidebar.selectbox("Select Game", list(game_opts.keys()), index=def_idx)
    sel_game     = game_opts[sel_game_lbl]
    game_pk      = sel_game["gamePk"]

    try:
        with st.spinner("Loading game feed…"):
            feed = fetch_live_feed(game_pk)
    except Exception as e:
        st.error(f"Could not load game feed: {e}")
        st.stop()

    pitcher_opts = pitcher_options_from_feed(feed)
    if not pitcher_opts:
        st.info("No pitch data found for this game yet.")
        st.stop()

    sel_p_lbl = st.sidebar.selectbox("Select Pitcher from Game", list(pitcher_opts.keys()))
    sel_pid, sel_pname = pitcher_opts[sel_p_lbl]

    season = game_date.year
    st.sidebar.markdown("---")
    st.sidebar.markdown(f"**Season:** {season} (auto)")

    # Load game pitches
    game_pitches = extract_pitcher_pitches(feed, target_pid=sel_pid)
    fg_row       = get_fg_row(sel_pname, season)

    # Load full season Statcast
    with st.spinner(f"Loading {sel_pname} {season} season data…"):
        try:
            sc_full = get_statcast(f"{season}-03-20", f"{season}-10-01", sel_pid)
            season_pitches = statcast_to_pitch_list(sc_full) if not sc_full.empty else []
        except Exception:
            season_pitches = []

    away_name = sel_game.get("teams", {}).get("away", {}).get("team", {}).get("name", "Away")
    home_name = sel_game.get("teams", {}).get("home", {}).get("team", {}).get("name", "Home")
    st.subheader(f"{sel_pname}  ·  {away_name} @ {home_name}  vs  {season} Season")

    # ── Delta heatmap table (always full-width, above side-by-side) ──
    st.markdown("### 📊 Game vs Season — Pitch Comparison")
    render_comparison_table(game_pitches, season_pitches,
                            label_left="This Game", label_right=str(season) + " Season")
    st.markdown("---")

    is_mobile = st.checkbox("📱 Mobile layout (tabs)", value=False)

    if is_mobile:
        tab_game, tab_season = st.tabs(["🎮 This Game", f"📅 {season} Season"])
        with tab_game:
            render_panel("This Game", game_pitches, sel_pname, season, fg_row, "This Game")
        with tab_season:
            render_panel(f"{season} Season", season_pitches, sel_pname, season, fg_row, f"{season} Full Season")
    else:
        col_game, col_season = st.columns(2, gap="large")
        with col_game:
            st.markdown("## 🎮 This Game")
            render_panel("This Game", game_pitches, sel_pname, season, fg_row, "This Game")
        with col_season:
            st.markdown(f"## 📅 {season} Season")
            render_panel(f"{season} Season", season_pitches, sel_pname, season, fg_row, f"{season} Full Season")
