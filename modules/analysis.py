# modules/analysis.py
from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import urlparse

import pandas as pd
import streamlit as st

from .utils import debug_log
from .drive import _ensure_ws, safe_set_df, copy_template_and_open
from .gsc import (
    consultar_datos,
    consultar_por_pais,
    fetch_gsc_monthly_by_page,
    fetch_site_daily_totals,
    fetch_gsc_daily_evergreen,
    fetch_gsc_daily_by_page,  # diario por URL genérico (web/discover)
)


# ========= Helpers comunes =========

def get_template_id(kind: str, account_key: str | None = None) -> str | None:
    """Lee el template ID desde st.secrets. Prioridad: templates[account_key][kind] → templates[kind]."""
    troot = st.secrets.get("templates", {})
    if account_key and isinstance(troot.get(account_key), dict):
        return troot[account_key].get(kind) or troot.get(kind)
    return troot.get(kind)


def compute_core_windows(lag_days, fecha_inicio: date, termino: str, fecha_fin: date | None):
    hoy_util = date.today() - timedelta(days=lag_days)
    if termino == "sí" and fecha_fin:
        dias_analisis = max((hoy_util - fecha_fin).days, 1)
        pre_ini = fecha_inicio - timedelta(days=dias_analisis)
        pre_fin = fecha_inicio - timedelta(days=1)
        post_ini = fecha_fin + timedelta(days=1)
        post_fin = fecha_fin + timedelta(days=dias_analisis)
    else:
        dias_analisis = max((hoy_util - fecha_inicio).days, 1)
        pre_ini = fecha_inicio - timedelta(days=dias_analisis)
        pre_fin = fecha_inicio - timedelta(days=1)
        post_ini = fecha_inicio
        post_fin = hoy_util
    return pre_ini, pre_fin, post_ini, post_fin


def _site_display_name(site_url: str) -> str:
    if site_url.startswith("http"):
        return urlparse(site_url).netloc.replace("www.", "")
    return site_url.replace("sc-domain:", "")


def _spanish_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Agrega columnas en español que suelen usar los templates:
    fecha, url, clics, impresiones, posición (mantiene ctr).
    No elimina las originales.
    """
    if df.empty:
        return df
    out = df.copy()
    # Normalizaciones seguras
    if "date" in out.columns:
        out["fecha"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    if "page" in out.columns:
        out["url"] = out["page"]
    if "clicks" in out.columns:
        out["clics"] = out["clicks"]
    if "impressions" in out.columns:
        out["impresiones"] = out["impressions"]
    if "position" in out.columns:
        out["posición"] = out["position"]

    # Orden sugerido: primero ES, luego originales
    desired = ["fecha", "url", "clics", "impresiones", "ctr", "posición", "period",
               "date", "page", "clicks", "impressions", "position"]
    cols = [c for c in desired if c in out.columns] + [c for c in out.columns if c not in desired]
    return out[cols]


# ========= Core Update =========

def run_core_update(sc_service, drive, gsclient, site_url, params, dest_folder_id=None):
    lag_days, f_ini, termino, f_fin, tipo, pais, seccion = params
    pre_ini, pre_fin, post_ini, post_fin = compute_core_windows(lag_days, f_ini, termino, f_fin)

    # Mapeo UI → API
    tipos = [("Search", "web"), ("Discover", "discover")] if tipo == "Ambos" else [
        ("Search", "web") if tipo == "Search" else ("Discover", "discover")
    ]

    nombre_medio = _site_display_name(site_url) or "sitio"
    nombre_analisis = "Análisis de impacto de Core Update"
    title = f"{nombre_medio} - {nombre_analisis} - {date.today()}"

    template_id = get_template_id("core_update", st.session_state.get("oauth", {}).get("account"))
    if not template_id:
        st.error("No se configuró el ID de template para 'core_update' en st.secrets.")
        st.stop()

    sh, sid = copy_template_and_open(drive, gsclient, template_id, title, dest_folder_id)
    st.caption(f"Períodos calculados → Pre: {pre_ini} a {pre_fin} | Post: {post_ini} a {post_fin}")

    wrote_any = False

    for tipo_nombre, tipo_val in tipos:
        # -------- PRE/POST por página (para Resumen) --------
        df_pre = consultar_datos(sc_service, site_url, pre_ini, pre_fin, tipo_val, pais=pais, seccion_filtro=seccion)
        df_post = consultar_datos(sc_service, site_url, post_ini, post_fin, tipo_val, pais=pais, seccion_filtro=seccion)
        debug_log(f"[{tipo_nombre}] filas PRE", len(df_pre))
        debug_log(f"[{tipo_nombre}] filas POST", len(df_post))

        if not df_pre.empty:
            df_pre_es = df_pre.rename(columns={"position": "posición", "impressions": "impresiones", "clicks": "clics"})
            ws_pre = _ensure_ws(sh, f"{tipo_nombre} | Pre")  # nombre esperado por template
            safe_set_df(ws_pre, df_pre_es)
            wrote_any = True
        else:
            st.info(f"Sin datos PRE para {tipo_nombre} con esos filtros.")

        if not df_post.empty:
            df_post_es = df_post.rename(columns={"position": "posición", "impressions": "impresiones", "clicks": "clics"})
            ws_post = _ensure_ws(sh, f"{tipo_nombre} | Post")  # nombre esperado por template
            safe_set_df(ws_post, df_post_es)
            wrote_any = True
        else:
            st.info(f"Sin datos POST para {tipo_nombre} con esos filtros.")

        # -------- Tráfico por país (apoyo) --------
        df_pre_p = consultar_por_pais(sc_service, site_url, pre_ini, pre_fin, tipo_val, seccion_filtro=seccion)
        df_post_p = consultar_por_pais(sc_service, site_url, post_ini, post_fin, tipo_val, seccion_filtro=seccion)
        debug_log(f"[{tipo_nombre}] por país PRE", len(df_pre_p))
        debug_log(f"[{tipo_nombre}] por país POST", len(df_post_p))

        dfp = df_pre_p.merge(df_post_p, on="country", how="outer", suffixes=("_pre", "_post")).fillna(0)
        if not dfp.empty:
            ws_tp = _ensure_ws(sh, f"{tipo_nombre} | Tráfico por país")
            safe_set_df(ws_tp, dfp)

        # -------- NUEVO: Datos Diarios (por URL) PRE+POST --------
        df_daily_pre = fetch_gsc_daily_by_page(
            sc_service, site_url, pre_ini, pre_fin, tipo=tipo_val, country_iso3=pais, section_path=seccion
        )
        df_daily_post = fetch_gsc_daily_by_page(
            sc_service, site_url, post_ini, post_fin, tipo=tipo_val, country_iso3=pais, section_path=seccion
        )

        if not df_daily_pre.empty:
            df_daily_pre.insert(0, "period", "Pre")
        if not df_daily_post.empty:
            df_daily_post.insert(0, "period", "Post")

        if not df_daily_pre.empty or not df_daily_post.empty:
            df_daily_all = pd.concat([df_daily_pre, df_daily_post], ignore_index=True)
            # Agregar columnas en español que suelen consumir las hojas Resumen
            df_daily_all = _spanish_columns(df_daily_all)
            debug_log(f"[{tipo_nombre}] Datos Diarios PRE+POST (filas)", len(df_daily_all))
            debug_log(f"[{tipo_nombre}] Datos Diarios columnas", list(df_daily_all.columns))

            ws_daily = _ensure_ws(sh, f"{tipo_nombre} | Datos Diarios")  # nombre exacto del template
            safe_set_df(ws_daily, df_daily_all)
            wrote_any = True
        else:
            st.info(f"Sin datos DIARIOS para {tipo_nombre} con esos filtros.")

    # -------- Configuración --------
    cfg = pd.DataFrame([
        ("Sitio Analizado", site_url),
        ("Tipo de análisis", ", ".join([t[0] for t in tipos])),
        ("Periodo Core Update", f"{f_ini} a {f_fin or ''}".strip()),
        ("Periodo Pre Core Update", f"{pre_ini} a {pre_fin}"),
        ("Periodo Post Core Update", f"{post_ini} a {post_fin}"),
        ("Sección", seccion or "Todo el sitio"),
        ("País", pais or "Todos"),
    ], columns=["Configuración", "Valor"])
    ws_cfg = _ensure_ws(sh, "Configuracion")
    safe_set_df(ws_cfg, cfg)

    if not wrote_any:
        st.warning(
            "No se exportó ninguna hoja con datos. Verificá:\n"
            "• Propiedad elegida (sc-domain vs URL-prefix)\n"
            "• Fechas (hay datos en GSC para ese rango?)\n"
            "• Tipo (Search/Discover) y filtros de país/sección"
        )

    return sid


# ========= Evergreen (sin cambios funcionales respecto a la última versión) =========

def run_evergreen(sc_service, drive, gsclient, site_url, params, dest_folder_id=None):
    lag_days, pais, seccion, incluir_diario, start_date, end_date = params

    nombre_medio = _site_display_name(site_url) or "sitio"
    nombre_analisis = "Análisis de tráfico evergreen"
    title = f"{nombre_medio} - {nombre_analisis} - {date.today()}"

    template_id = get_template_id("evergreen", st.session_state.get("oauth", {}).get("account"))
    if not template_id:
        st.error("No se configuró el ID de template para 'evergreen' en st.secrets.")
        st.stop()

    sh, sid = copy_template_and_open(drive, gsclient, template_id, title, dest_folder_id)

    # Mensual por página (Search/web)
    monthly = fetch_gsc_monthly_by_page(
        sc_service, site_url, start_date, end_date, country_iso3=pais, section_path=seccion
    )
    debug_log("[Evergreen] filas mensuales", len(monthly))
    if monthly.empty:
        st.info("Sin datos mensuales (Search) en ese período/filtros.")
    else:
        ws_month = _ensure_ws(sh, "Search | Datos mensuales")
        safe_set_df(ws_month, monthly)

    # Totales diarios del sitio (Search/web)
    daily_tot = fetch_site_daily_totals(
        sc_service, site_url, start_date, end_date, country_iso3=pais, section_path=seccion
    )
    debug_log("[Evergreen] filas diario total", len(daily_tot))
    if daily_tot.empty:
        st.info("Sin datos diarios totales (Search) en ese período/filtros.")
    else:
        ws_total = _ensure_ws(sh, "Search | Diario total")
        safe_set_df(ws_total, daily_tot)

    # (Opcional) Diario por URL — nombre EXACTO para el template
    if incluir_diario:
        df_daily = fetch_gsc_daily_evergreen(
            sc_service, site_url, start_date, end_date, country_iso3=pais, section_path=seccion
        )
        debug_log("[Evergreen] filas diario por URL", len(df_daily))
        if df_daily.empty:
            st.info("Sin datos diarios por URL (Search) en ese período/filtros.")
        else:
            df_daily = _spanish_columns(df_daily)
            ws_daily = _ensure_ws(sh, "Search | Datos Diarios")   # mayúscula D
            safe_set_df(ws_daily, df_daily)

    # Configuración
    cfg = pd.DataFrame([
        ("Sitio Analizado", site_url),
        ("Ventana mensual", f"{start_date} a {end_date}"),
        ("Sección", seccion or "Todo el sitio"),
        ("País", pais or "Todos"),
        ("Incluye diario por URL", "Sí" if incluir_diario else "No"),
    ], columns=["Configuración", "Valor"])
    ws_cfg = _ensure_ws(sh, "Configuracion")
    safe_set_df(ws_cfg, cfg)

    return sid
