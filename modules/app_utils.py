# modules/app_utils.py
from __future__ import annotations

import streamlit as st

# Intentamos importar desde la raíz; si no existe, usamos un fallback seguro.
try:
    from app_constants import SCOPES_GSC  # type: ignore
except Exception:
    SCOPES_GSC = ["https://www.googleapis.com/auth/webmasters.readonly"]

# Scope(s) de Google Analytics 4 (GA4)
try:
    from app_constants import SCOPES_GA4  # type: ignore
except Exception:
    SCOPES_GA4 = ["https://www.googleapis.com/auth/analytics.readonly"]

def get_qp() -> dict:
    """Obtiene los query params de forma compatible con distintas versiones de Streamlit."""
    try:
        return dict(st.query_params)
    except Exception:
        return st.experimental_get_query_params()

def clear_qp() -> None:
    """Limpia los query params de forma compatible con distintas versiones de Streamlit."""
    try:
        st.query_params.clear()
    except Exception:
        st.experimental_set_query_params()

@st.cache_resource
def oauth_flow_store() -> dict:
    """Almacén en memoria para objetos Flow (sobrevive a reruns y pestañas)."""
    return {}

def has_gsc_scope(scopes: list[str] | None) -> bool:
    """Verifica si las credenciales incluyen permisos de Search Console."""
    if not scopes:
        return False
    needed = set(SCOPES_GSC + ["https://www.googleapis.com/auth/webmasters"])
    return any(s in needed for s in scopes)

def norm(s: str | None) -> str:
    """Normaliza strings para comparar etiquetas de cuentas."""
    if not s:
        return ""
    return "".join(ch for ch in s.lower() if ch.isalnum())
