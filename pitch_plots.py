import pandas as pd
import streamlit as st
import plotly.express as px
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

pitch_colors_mapping = {
    "FF": "#FF007D",
    "SI": "#98165D",
    "FC": "#BE5FA0",
    "CH": "#F79E70",
    "FS": "#FE6100",
    "SC": "#F08223",
    "FO": "3FFB000",
    "SL": "#67E18D",
    "ST": "#1BB999",
    "SV": "#376748",
    "KC": "#311D8B",
    "CU": "#3025CE",
    "CS": "#274BFC",
    "EP": "#648FFF",
    "KN": "#867A08",
    "PO": "#472C30",
    "UN": "#9C8975"
}


@st.cache_data
def get_filtered_data(start_date, end_date, playerid):
    return statcast_pitcher(start_dt=start_date, end_dt=end_date,player_id=playerid)

st.title("Pitch Plots Horizontal and Vertical Movement")

player_name = st.text_input("Enter Player Name", value="Hunter Greene")
if player_name:
    split_name = player_name.split(" ")
    first_name = split_name[0]
    last_name = split_name[1] if len(player_name.split(" ")) > 1 else ""
    player_info = playerid_lookup(last_name, first_name)
    try:
        first_name, last_name = player_name.split(" ")
        player_info = playerid_lookup(last_name, first_name)
    except ValueError:
        st.error("Enter a valid first and last name")
        st.stop()
    
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
else:
    st.stop()


start_date = st.date_input("Start Date", value=pd.to_datetime("2024-03-20"))
end_date = st.date_input("End Date", value=pd.to_datetime("2024-09-30"))

if start_date > end_date:
    st.error("Start date must be before or equal to the end date.")
    st.stop()

# Fetch data based on user inputs
with st.spinner("Fetching data..."):
    data = get_filtered_data(
        start_date=start_date.strftime("%Y-%m-%d"), 
        end_date=end_date.strftime("%Y-%m-%d"), 
        playerid = playerid)
    data["pfx_x"]=data["pfx_x"]*-12
    data["pfx_z"]=data["pfx_z"]*12

if data.empty:
    st.warning("No data available for the selected player and date range")
else:
    #Display success message
    st.success(f"Data loaded successfully for the range {start_date} to {end_date}.")
    #Debug or display data as needed
    st.write("### Pitch Movement Chart")    

    #Scatter plot of pitch movement
    #Scatter plot of pitch movement
    scatter_plot = px.scatter(
        data,
        x="pfx_x",
        y="pfx_z",
        color="pitch_name",
        title="Pitch Movement",
        labels={"pfx_x": "Horizontal Break (inches)","pfx_z": "Vertical Break (inches)"},
        hover_data=["release_speed","type"],
        color_discrete_map = pitch_colors_mapping
    )
    scatter_plot.update_xaxes(tick0 = -2,dtick=.5)
    scatter_plot.update_yaxes(tick0 = -2,dtick=.5)


    st.plotly_chart(scatter_plot, use_container_width=True)


