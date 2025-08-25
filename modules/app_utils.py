import streamlit as st
from app_constants import SCOPES_GSC

def get_qp() -> dict:
    try:
        return dict(st.query_params)
    except Exception:
        return st.experimental_get_query_params()

def clear_qp():
    try:
        st.query_params.clear()
    except Exception:
        st.experimental_set_query_params()

@st.cache_resource
def oauth_flow_store():
    # Almacén global de OAuth Flows (compartido entre pestañas/sesiones)
    return {}

def has_gsc_scope(scopes: list[str] | None) -> bool:
    if not scopes:
        return False
    needed = set(SCOPES_GSC)
    return any(s in scopes for s in needed) or "https://www.googleapis.com/auth/webmasters" in (scopes or [])

def norm(s: str | None) -> str:
    if not s: return ""
    return "".join(ch for ch in s.lower() if ch.isalnum())