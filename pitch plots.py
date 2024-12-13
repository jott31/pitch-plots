import pandas as pd
import streamlit as st
from pybaseball import statcast
from pybaseball import statcast_pitcher
from pybaseball import playerid_lookup

pitch_type_mapping = {
    "FF": "Four-Seam Fastball",
    "SL": "Slider",
    "CU": "Curveball",
    "CH": "Changeup",
    "FS": "Splitter",
    "SI": "Sinker",
    "FC": "Cutter",
    "KC": "Knuckle Curve",
    "KN": "Knuckleball",
    "SV": "Sweeper",
    "ST": "Sweeping Curve",
    "CS": "Slow Curve",
}

@st.cache_data
def get_filtered_data(start_date, end_date, playerid):
    return statcast_pitcher(start_dt=start_date, end_dt=end_date,player_id=playerid, parallel=True)

st.title("Pitch Plots Horizontal and Vertical Movement")

player_name = st.text_input("Enter Player Name", value="Hunter Greene")
if player_name:
    player_info = playerid_lookup(player_name.split(" ")[1], player_name.split(" "[0]))
    if not player_info.empty:
        named_id_mapping = {
            f"{row['name_first']} {row['name_last']}": row["key_mlbam"]
            for _, row in player_info.iterrows()
        }

        selected_player_name = st.selectbox(
            "Select player ID",
            options= named_id_mapping.keys()
        )
        playerid =  named_id_mapping[selected_player_name]
    else:
        st.error("No players found")
        st.stop()
start_date = st.date_input("Start Date", value=pd.to_datetime("2024-06-01"))
end_date = st.date_input("End Date", value=pd.to_datetime("2024-06-30"))

if start_date > end_date:
    st.error("Start date must be before or equal to the end date.")
else:
    # Fetch data based on user inputs
    with st.spinner("Fetching data..."):
        data = get_filtered_data(start_date=start_date.strftime("%Y-%m-%d"), 
                                 end_date=end_date.strftime("%Y-%m-%d"), 
                                 playerid = playerid)

    if not data.empty:

        # Display success message
        st.success(f"Data loaded successfully for the range {start_date} to {end_date}.")

    # Debug or display data as needed
        st.write("### Pitch Movement Chart")
    else:
        st.warning("No data available for the selected player and date range")

import pandas as pd
import streamlit as st
from pybaseball import statcast
from pybaseball import statcast_pitcher
from pybaseball import playerid_lookup

pitch_type_mapping = {
    "FF": "Four-Seam Fastball",
    "SL": "Slider",
    "CU": "Curveball",
    "CH": "Changeup",
    "FS": "Splitter",
    "SI": "Sinker",
    "FC": "Cutter",
    "KC": "Knuckle Curve",
    "KN": "Knuckleball",
    "SV": "Sweeper",
    "ST": "Sweeping Curve",
    "CS": "Slow Curve",
}

@st.cache_data
def get_filtered_data(start_date, end_date, playerid):
    return statcast_pitcher(start_dt=start_date, end_dt=end_date,player_id=playerid, parallel=True)

playerid = st.selectbox("Player Name", value = "" + playerid_lookup("Hunter Greene").head(10))
start_date = st.date_input("Start Date", value=pd.to_datetime("2024-06-01"))
end_date = st.date_input("End Date", value=pd.to_datetime("2024-06-30"))

if start_date > end_date:
    st.error("Start date must be before or equal to the end date.")
else:
    # Fetch data based on user inputs
    with st.spinner("Fetching data..."):
        data = get_filtered_data(start_date=start_date.strftime("%Y-%m-%d"), end_date=end_date.strftime("%Y-%m-%d"), playerid = playerid_lookup(playerid))

    # Display success message
    st.success(f"Data loaded successfully for the range {start_date} to {end_date}.")

    # Debug or display data as needed
    st.write(data.head())

st.title("Pitch Plots Savant Data")

st.scatter_chart(x= data["pfx_x"], y=data["pfx_z"])
