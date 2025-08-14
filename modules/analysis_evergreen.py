# modules/analysis_evergreen.py
from __future__ import annotations
from datetime import date
from urllib.parse import urlparse
import pandas as pd
import streamlit as st

from .utils import debug_log
from .drive import _ensure_ws, safe_set_df, copy_template_and_open
from .gsc import (
    fetch_gsc_monthly_by_page,
    fetch_site_daily_totals,
    fetch_gsc_daily_evergreen,
)

def _site_display_name(site_url: str) -> str:
    if site_url.startswith("http"):
        return urlparse(site_url).netloc.replace("www.", "")
    return site_url.replace("sc-domain:", "")

def _template_id(kind: str, account_key: str | None = None) -> str | None:
    root = st.secrets.get("templates", {})
    if account_key and isinstance(root.get(account_key), dict):
        return root[account_key].get(kind) or root.get(kind)
    return root.get(kind)

def run_evergreen(sc_service, drive, gsclient, site_url, params, dest_folder_id=None):
    lag_days, pais, seccion, incluir_diario, start_date, end_date = params

    nombre_medio = _site_display_name(site_url) or "sitio"
    title = f"{nombre_medio} - Análisis de tráfico evergreen - {date.today()}"

    template_id = _template_id("evergreen", st.session_state.get("oauth", {}).get("account"))
    if not template_id:
        st.error("No se configuró el ID de template para 'evergreen' en st.secrets.")
        st.stop()

    sh, sid = copy_template_and_open(drive, gsclient, template_id, title, dest_folder_id)

    monthly = fetch_gsc_monthly_by_page(sc_service, site_url, start_date, end_date, country_iso3=pais, section_path=seccion)
    debug_log("[Evergreen] filas mensuales", len(monthly))
    if not monthly.empty:
        safe_set_df(_ensure_ws(sh, "Search | Datos mensuales"), monthly)
    else:
        st.info("Sin datos mensuales (Search) en ese período/filtros.")

    daily_tot = fetch_site_daily_totals(sc_service, site_url, start_date, end_date, country_iso3=pais, section_path=seccion)
    debug_log("[Evergreen] filas diario total", len(daily_tot))
    if not daily_tot.empty:
        safe_set_df(_ensure_ws(sh, "Search | Diario total"), daily_tot)
    else:
        st.info("Sin datos diarios totales (Search) en ese período/filtros.")

    if incluir_diario:
        df_daily = fetch_gsc_daily_evergreen(sc_service, site_url, start_date, end_date, country_iso3=pais, section_path=seccion)
        debug_log("[Evergreen] filas diario por URL", len(df_daily))
        if not df_daily.empty:
            safe_set_df(_ensure_ws(sh, "Search | Datos Diarios"), df_daily)
        else:
            st.info("Sin datos diarios por URL (Search) en ese período/filtros.")

    cfg = pd.DataFrame([
        ("Sitio Analizado", site_url),
        ("Ventana mensual", f"{start_date} a {end_date}"),
        ("Sección", seccion or "Todo el sitio"),
        ("País", pais or "Todos"),
        ("Incluye diario por URL", "Sí" if incluir_diario else "No"),
    ], columns=["Configuración", "Valor"])
    safe_set_df(_ensure_ws(sh, "Configuracion"), cfg)

    return sid
