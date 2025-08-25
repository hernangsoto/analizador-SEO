# modules/app_config.py
from __future__ import annotations
import streamlit as st
from modules.ui import apply_page_style
from modules.app_constants import HEADER_COLOR, HEADER_HEIGHT, LOGO_URL

def _pin_nomadic_logo_css(logo_url: str):
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

def setup_page_and_branding():
    st.set_page_config(layout="wide", page_title="Análisis SEO", page_icon="📊")
    apply_page_style(
        header_bg=HEADER_COLOR,
        header_height_px=HEADER_HEIGHT,
        page_bg="#