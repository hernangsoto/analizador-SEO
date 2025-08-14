# modules/auth.py
import streamlit as st
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from modules.utils import debug_log, token_store

# ... (SCOPES_* y helpers existentes)

def _streamlit_email():
    u = getattr(st, "user", getattr(st, "experimental_user", None))
    return getattr(u, "email", None)

def get_cached_personal_creds() -> Credentials | None:
    """
    Devuelve credenciales de Drive/Sheets para la cuenta personal SIN mostrar UI,
    reutilizando token del store de servidor o de la sesión, y refrescándolo si hace falta.
    """
    user_email = _streamlit_email()
    cache = st.session_state.setdefault("creds_cache", {"dest": {}, "src": {}})
    store = token_store()

    creds = None
    # 1) Store (servidor)
    if user_email and store["dest"].get(user_email):
        creds = Credentials(**store["dest"][user_email])
        try:
            if not creds.valid and getattr(creds, "refresh_token", None):
                creds.refresh(Request())
        except Exception as e:
            debug_log("No se pudo refrescar token (store dest)", str(e))
    # 2) Cache de sesión (si el store no sirvió)
    if not (creds and creds.valid) and user_email and cache["dest"].get(user_email):
        creds = Credentials(**cache["dest"][user_email])
        try:
            if not creds.valid and getattr(creds, "refresh_token", None):
                creds.refresh(Request())
        except Exception as e:
            debug_log("No se pudo refrescar token (session dest)", str(e))

    if creds and creds.valid:
        # Normalizar a session_state para el resto de la app
        st.session_state["creds_dest"] = {
            "token": creds.token,
            "refresh_token": getattr(creds, "refresh_token", None),
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": list(creds.scopes) if getattr(creds, "scopes", None) else SCOPES_DRIVE,
            "expiry": getattr(creds, "expiry", None),
        }
        return creds
    return None
