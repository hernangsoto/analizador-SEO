# modules/lottie.py
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from typing import Any, Dict, Optional

import requests
import streamlit as st
from streamlit_lottie import st_lottie


@st.cache_data(show_spinner=False)
def _load_lottie_from_url(url: str) -> Optional[Dict[str, Any]]:
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


@st.cache_data(show_spinner=False)
def _load_lottie_from_file(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def lottie(json_or_url_or_path: Any, **kwargs):
    """
    Renderiza una animación Lottie.
    - Si recibe un dict -> lo usa directo.
    - Si recibe un string con 'http' -> lo baja de URL.
    - Si recibe un path a .json -> lo lee del disco.
    kwargs típicos: height=140, loop=True, speed=1.0, quality="high"
    """
    data = None
    if isinstance(json_or_url_or_path, dict):
        data = json_or_url_or_path
    elif isinstance(json_or_url_or_path, str):
        if json_or_url_or_path.startswith("http"):
            data = _load_lottie_from_url(json_or_url_or_path)
        else:
            # Asumimos archivo local .json
            data = _load_lottie_from_file(json_or_url_or_path)

    if not data:
        st.warning("No se pudo cargar la animación Lottie.")
        return None

    return st_lottie(data, **kwargs)


@contextmanager
def lottie_spinner(json_or_url_or_path: Any, text: str = "Procesando…", **kwargs):
    """
    Muestra una animación Lottie como “loader” mientras se ejecuta un bloque:
        with lottie_spinner(LOTTIE_URL, text="Extrayendo datos..."):
            # tu tarea pesada aquí
    """
    ph = st.empty()
    with ph.container():
        lottie(json_or_url_or_path, height=140, loop=True, **kwargs)
        if text:
            st.caption(text)
    try:
        yield
    finally:
        ph.empty()
