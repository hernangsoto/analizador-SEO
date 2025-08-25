# modules/app_constants.py
from __future__ import annotations
import os
import streamlit as st

HEADER_COLOR = "#5c417c"
HEADER_HEIGHT = 64
LOGO_URL = "https://nomadic.agency/wp-content/uploads/2021/03/logo-blanco.png"

def resolve_gemini_model() -> str:
    return (
        os.getenv("GEMINI_MODEL")
        or st.secrets.get("GEMINI_MODEL")
        or st.secrets.get("gemini", {}).get("model")
        or "gemini-2.5-flash"
    )

# Deja el modelo elegido disponible en el entorno
os.environ["GEMINI_MODEL"] = resolve_gemini_model()

APP_HOME = st.secrets.get("app_home_url", "https://hernangsoto.streamlit.app")

SCOPES_GSC = ["https://www.googleapis.com/auth/webmasters.readonly"]

LAG_DAYS_DEFAULT = 3