import streamlit as st

st.set_page_config(page_title="Pitch Analytics", page_icon="⚾", layout="wide")

# This runs before every page — inject background color here so it's
# always present in the app shell, preventing any white flash on navigation.
st.markdown("""
    <style>
        html, body, [data-testid="stApp"] { background-color: #0e1117 !important; }
        [data-testid="stSidebar"]        { display: none; }
        [data-testid="collapsedControl"] { display: none; }
    </style>
""", unsafe_allow_html=True)

pg = st.navigation(
    [
        st.Page("pages/Season_Data.py", title="Season Stats",  icon="⚾", default=True),
        st.Page("pages/Live_Games.py",  title="Live Games",    icon="🔴"),
        st.Page("pages/Compare.py",     title="Compare",       icon="📊"),
    ],
    position="hidden",   # hide the built-in sidebar nav — we use our own inline nav bar
)
pg.run()
