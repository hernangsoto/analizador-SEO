# modules/app_utils.py
from __future__ import annotations
import unicodedata
from typing import Dict, List, Iterable, Any

import streamlit as st

# Scopes usados por la app
GSC_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
GA4_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"


def get_qp() -> Dict[str, List[str]]:
    """
    Devuelve los query params como dict[str, list[str]].
    Soporta Streamlit nuevo (st.query_params) y versiones previas (experimental_*).
    """
    # Streamlit >= 1.30: st.query_params es un Mapping "vivo"
    try:
        qp = st.query_params  # type: ignore[attr-defined]
        out: Dict[str, List[str]] = {}
        for k, v in qp.items():
            out[k] = v if isinstance(v, list) else [v]
        return out
    except Exception:
        pass

    # Fallback: APIs experimentales antiguas
    try:
        return st.experimental_get_query_params()  # type: ignore[attr-defined]
    except Exception:
        return {}


def clear_qp() -> None:
    """Limpia los query params, con compatibilidad hacia atrás."""
    # Streamlit >= 1.30
    try:
        st.query_params.clear()  # type: ignore[attr-defined]
        return
    except Exception:
        pass

    # Fallback antiguo
    try:
        st.experimental_set_query_params()  # type: ignore[attr-defined]
    except Exception:
        pass


def has_gsc_scope(scopes: Iterable[str] | None) -> bool:
    """Chequea si el token tiene scope de Search Console (lectura)."""
    if not scopes:
        return False
    base = GSC_SCOPE.split("?")[0]
    return any(str(s or "").startswith(base) for s in scopes)


def has_ga4_scope(scopes: Iterable[str] | None) -> bool:
    """Chequea si el token tiene scope de GA4 (lectura)."""
    if not scopes:
        return False
    base = GA4_SCOPE.split("?")[0]
    return any(str(s or "").startswith(base) for s in scopes)


def norm(s: Any) -> str:
    """
    Normaliza para comparaciones "suaves":
    - str(), trim, lower
    - quita acentos/diacríticos
    - colapsa espacios internos
    """
    if s is None:
        return ""
    txt = str(s).strip().lower()
    txt = unicodedata.normalize("NFKD", txt)
    txt = "".join(ch for ch in txt if not unicodedata.combining(ch))
    return " ".join(txt.split())
