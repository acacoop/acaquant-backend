"""Autenticación del dashboard: lectura del email inyectado por Cloudflare Access."""
import streamlit as st
from config import MANAGER_EMAILS


def get_user_email() -> str:
    """
    Lee el email autenticado por Cloudflare Access del header HTTP.
    En desarrollo local (sin Cloudflare) devuelve string vacío.
    """
    try:
        headers = st.context.headers
        return headers.get("Cf-Access-Authenticated-User-Email", "").strip().lower()
    except Exception:
        return ""


def is_manager_allowed() -> bool:
    """Devuelve True si el usuario actual tiene acceso al Manager."""
    if not MANAGER_EMAILS:
        return True  # Si no hay lista configurada, permite acceso (modo dev local)
    return get_user_email() in MANAGER_EMAILS
