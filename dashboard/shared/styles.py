"""CSS global del dashboard (sidebar)."""
import streamlit as st

SIDEBAR_CSS = """
<style>
[data-testid="stSidebar"] {
    background-color: #094293;
}
[data-testid="stSidebar"] * {
    color: #ffffff !important;
}
[data-testid="stSidebar"] .stRadio label {
    color: #ffffff !important;
}
[data-testid="stSidebar"] hr {
    border-color: rgba(255,255,255,0.3);
}
/* Fondo blanco solo en el bloque donde vive la imagen del logo */
[data-testid="stSidebar"] [data-testid="stImage"] {
    background-color: #ffffff;
    padding: 12px;
    border-radius: 0 0 8px 8px;
}
</style>
"""


def apply_sidebar_styles():
    st.markdown(SIDEBAR_CSS, unsafe_allow_html=True)
