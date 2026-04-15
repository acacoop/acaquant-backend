import streamlit as st

from dashboard.shared.auth import is_manager_allowed
from dashboard.shared.styles import apply_sidebar_styles
from dashboard.views.aum import vista_aum
from dashboard.views.manager import vista_data_manager
from dashboard.views.mercado import vista_mercado
from dashboard.views.opciones import vista_opciones
from dashboard.views.operaciones import vista_operaciones
from dashboard.views.portfolios import vista_portfolios

# ==========================================
# CONFIG
# ==========================================
st.set_page_config(
    layout="wide",
    page_title="ACAQuant | Mesa de Dinero",
    page_icon="📈",
    initial_sidebar_state="expanded"
)

apply_sidebar_styles()


# ==========================================
# SIDEBAR - NAVEGACIÓN
# ==========================================
with st.sidebar:
    st.image("images/logo-header.png", use_container_width=True)
    st.markdown("---")
    _opciones_nav = ["Mercado", "Opciones", "Portfolios", "Operaciones", "AuM"]
    if is_manager_allowed():
        _opciones_nav.append("Manager")
    vista = st.radio(
        "Vista",
        _opciones_nav,
        label_visibility="collapsed"
    )


# Ruteo: solo se llama el fragmento activo.
if vista == "Opciones":
    vista_opciones()
elif vista == "Mercado":
    vista_mercado()
elif vista == "Portfolios":
    vista_portfolios()
elif vista == "Operaciones":
    vista_operaciones()
elif vista == "AuM":
    vista_aum()
elif vista == "Manager":
    if is_manager_allowed():
        vista_data_manager()
    else:
        st.error("Acceso denegado.")
