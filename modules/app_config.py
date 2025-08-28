# modules/app_config.py
import os
import streamlit as st
from modules.ui import apply_page_style

HEADER_COLOR = "#5c417c"
HEADER_HEIGHT = 64
LOGO_URL = "https://nomadic.agency/wp-content/uploads/2021/03/logo-blanco.png"

def _resolve_gemini_model() -> str:
    """Obtiene el modelo de Gemini desde env/secrets con fallback."""
    return (
        os.getenv("GEMINI_MODEL")
        or st.secrets.get("GEMINI_MODEL")
        or st.secrets.get("gemini", {}).get("model")
        or "gemini-2.5-flash"
    )

def _inject_sidebar_observer_js():
    """
    Define la variable CSS --sb-offset con el ancho actual del sidebar
    y la actualiza cuando se abre/cierra o cambia de tamaño.
    """
    st.markdown(
        """
        <script>
        (function() {
          const doc = window.parent?.document || document;

          function setSbOffset() {
            const sb = doc.querySelector('[data-testid="stSidebar"]');
            const w = sb ? sb.getBoundingClientRect().width : 0;
            doc.documentElement.style.setProperty('--sb-offset', (w > 1 ? w : 0) + 'px');
          }

          // Primera medición
          setSbOffset();

          // Observar cambios de tamaño del sidebar
          const target = doc.querySelector('[data-testid="stSidebar"]') || doc.body;
          new ResizeObserver(setSbOffset).observe(target);

          // Ajustar también en resize de ventana
          window.addEventListener('resize', setSbOffset);
        })();
        </script>
        """,
        unsafe_allow_html=True,
    )

def _pin_nomadic_logo_css(logo_url: str):
    """
    Pega el logo sobre el header nativo de Streamlit.
    La posición horizontal reacciona al ancho del sidebar (--sb-offset).
    """
    st.markdown(
        f"""
        <style>
        /* Mantener el header por encima del contenido */
        header[data-testid="stHeader"] {{
            position: sticky !important;
            top: 0;
            z-index: 1500 !important;
            background: transparent; /* El color de banda lo da apply_page_style */
        }}

        /* Oculta el logo nativo de Streamlit para no duplicar */
        header [data-testid="stLogo"] img {{
            opacity: 0 !important;
            pointer-events: none !important;
        }}

        /* Logo Nomadic como pseudo-element del header */
        header[data-testid="stHeader"]::before {{
            content: "";
            position: fixed;
            /* Se desplaza según el ancho actual del sidebar */
            left: calc(var(--sb-offset, 0px) + 16px);
            top: 14px;
            width: 140px;
            height: 27px;
            background-image: url('{logo_url}');
            background-repeat: no-repeat;
            background-size: contain;
            pointer-events: none;
            z-index: 4000;
            transition: left 180ms ease;  /* suave al abrir/cerrar sidebar */
        }}

        /* Responsive */
        @media (max-width: 600px) {{
            header[data-testid="stHeader"]::before {{
                left: calc(var(--sb-offset, 0px) + 12px);
                top: 12px;
                width: 120px;
                height: 24px;
            }}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )

def _inject_global_styles():
    """Botones y alertas con la paleta de Nomadic + header por encima del contenido."""
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

        /* Mantener el header siempre por encima */
        header[data-testid="stHeader"] {{ z-index:1500 !important; }}
        </style>
        """,
        unsafe_allow_html=True,
    )

def apply_base_style_and_logo():
    """
    Aplica el estilo base de la app, fija el logo en el header
    y deja GEMINI_MODEL disponible para otros módulos.
    """
    # Expone el modelo de Gemini para el resto del código
    os.environ["GEMINI_MODEL"] = _resolve_gemini_model()

    # Estilo general + banda superior
    apply_page_style(
        header_bg=HEADER_COLOR,
        header_height_px=HEADER_HEIGHT,
        page_bg="#ffffff",
        use_gradient=False,
        band_height_px=110,
    )

    # Primero observamos el sidebar y publicamos --sb-offset
    _inject_sidebar_observer_js()

    # Luego inyectamos el logo que usa esa variable
    _pin_nomadic_logo_css(LOGO_URL)

    # CSS global de la app
    _inject_global_styles()

def get_app_home() -> str:
    """URL base de la app (para enlaces internos que deben ir a la home)."""
    return st.secrets.get("app_home_url", "https://hernangsoto.streamlit.app")

__all__ = [
    "apply_base_style_and_logo",
    "get_app_home",
    "HEADER_COLOR",
    "HEADER_HEIGHT",
    "LOGO_URL",
]