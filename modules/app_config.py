# modules/app_config.py
from __future__ import annotations

import os
import streamlit as st
from modules.ui import apply_page_style

# ==== Constantes de branding ====
HEADER_COLOR = "#5c417c"
HEADER_HEIGHT = 64
LOGO_URL = "https://nomadic.agency/wp-content/uploads/2021/03/logo-blanco.png"

__all__ = [
    "HEADER_COLOR",
    "HEADER_HEIGHT",
    "LOGO_URL",
    "setup_page_and_branding",
    "apply_base_style_and_logo",
    "get_app_home",
]

# -------------------------------
# Helpers internos
# -------------------------------
def _resolve_gemini_model() -> str:
    return (
        os.getenv("GEMINI_MODEL")
        or st.secrets.get("GEMINI_MODEL")
        or st.secrets.get("gemini", {}).get("model")
        or "gemini-2.5-flash"
    )

def _pin_nomadic_logo_css(logo_url: str) -> None:
    st.markdown(
        f"""
        <style>
        header[data-testid="stHeader"] {{
            position: sticky !important;
            top: 0;
            z-index: 1500 !important;
        }}
        header[data-testid="stHeader"]::before {{
            content: "";
            position: fixed;
            left: 40px;
            top: 14px;
            width: 140px;
            height: 27px;
            background-image: url('{logo_url}');
            background-repeat: no-repeat;
            background-size: contain;
            pointer-events: none;
            z-index: 4000;
        }}
        @media (max-width: 600px) {{
            header[data-testid="stHeader"]::before {{
                left: 16px;
                top: 12px;
                width: 120px;
                height: 24px;
            }}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )

def _inject_global_styles() -> None:
    st.markdown(
        """
        <style>
        .stButton > button, .stDownloadButton > button {
          background: #8e7cc3 !important; border-color: #8e7cc3 !important;
          color: #fff !important; border-radius: 8px !important;
        }
        .stButton > button:hover, .stDownloadButton > button:hover { filter: brightness(0.93); }

        .success-inline {
          background:#e6f4ea; border:1px solid #a5d6a7; color:#1e4620;
          padding:10px 14px; border-radius:8px; display:flex; align-items:center; gap:.5rem; flex-wrap:wrap;
        }
        .success-inline a { color:#0b8043; text-decoration:underline; font-weight:600; }
        .success-inline strong { margin-left:.25rem; }

        header[data-testid="stHeader"] { z-index:1500 !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )

# -------------------------------
# API pública del módulo
# -------------------------------
def apply_base_style_and_logo() -> None:
    """
    Aplica estilos base de la marca y deja el modelo de Gemini
    resuelto en la variable de entorno GEMINI_MODEL.
    """
    # Exponer el modelo para otros módulos
    os.environ["GEMINI_MODEL"] = _resolve_gemini_model()

    # Estilo general + header nativo
    apply_page_style(
        header_bg=HEADER_COLOR,
        header_height_px=HEADER_HEIGHT,
        page_bg="#ffffff",
        use_gradient=False,
        band_height_px=110,
    )
    _pin_nomadic_logo_css(LOGO_URL)
    _inject_global_styles()

def get_app_home() -> str:
    """Devuelve la URL base de la app para enlaces internos."""
    return st.secrets.get("app_home_url", "https://hernangsoto.streamlit.app")

def setup_page_and_branding() -> dict:
    """
    Configura la página (título, favicon, layout), aplica la
    identidad visual y devuelve valores útiles para el app.

    Returns:
        dict: {"APP_HOME", "HEADER_COLOR", "HEADER_HEIGHT",
               "LOGO_URL", "GEMINI_MODEL"}
    """
    # Puede lanzarse solo una vez por sesión; ignorar si ya fue llamado
    try:
        st.set_page_config(layout="wide", page_title="Análisis SEO", page_icon="📊")
    except Exception:
        pass

    # Estilos + logo + GEMINI_MODEL en env
    apply_base_style_and_logo()

    # Título visible (seguro repetir)
    st.title("Analizador SEO 🚀")

    app_home = get_app_home()
    return {
        "APP_HOME": app_home,
        "HEADER_COLOR": HEADER_COLOR,
        "HEADER_HEIGHT": HEADER_HEIGHT,
        "LOGO_URL": LOGO_URL,
        "GEMINI_MODEL": os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
    }