# modules/analysis_core_update.py
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
    fetch_gsc_daily_by_page,  # diario por URL genérico (web/discover)
)

# -------- helpers internos --------
def _site_display_name(site_url: str) -> str:
    if site_url.startswith("http"):
        return urlparse(site_url).netloc.replace("www.", "")
    return site_url.replace("sc-domain:", "")

def _extract_section_from_url(u: str) -> str:
    try:
        p = urlparse(u)
        parts = [seg for seg in (p.path or "/").split("/") if seg]
        return f"/{parts[0]}/" if parts else "/"
    except Exception:
        return "/"

def _spanish_columns_daily(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    if "date" in out.columns:
        out["fecha"] = pd.to_datetime(out["date"]).dt.date
    g = out.groupby("fecha", as_index=False)[["clicks", "impressions"]].sum()
    g["ctr"] = (g["clicks"] / g["impressions"]).fillna(0)
    return g.rename(columns={"clicks": "clics", "impressions": "impresiones"})[["fecha", "clics", "impresiones", "ctr"]]

def _spanish_columns_prepost(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.rename(columns={"position": "posición", "impressions": "impresiones", "clicks": "clics"}).copy()
    if "url" not in out.columns and "page" in out.columns:
        out["url"] = out["page"]
    out["sección"] = out["url"].map(_extract_section_from_url)
    cols = ["url", "clics", "impresiones", "ctr", "posición", "sección"]
    for c in cols:
        if c not in out.columns:
            out[c] = "" if c in ("sección",) else 0
    return out[cols]

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

def _template_id(kind: str, account_key: str | None = None) -> str | None:
    root = st.secrets.get("templates", {})
    if account_key and isinstance(root.get(account_key), dict):
        return root[account_key].get(kind) or root.get(kind)
    return root.get(kind)

# -------- público: run_core_update --------
def run_core_update(sc_service, drive, gsclient, site_url, params, dest_folder_id=None):
    lag_days, f_ini, termino, f_fin, tipo, pais, seccion = params
    pre_ini, pre_fin, post_ini, post_fin = compute_core_windows(lag_days, f_ini, termino, f_fin)

    tipos = [("Search", "web"), ("Discover", "discover")] if tipo == "Ambos" else [
        ("Search", "web") if tipo == "Search" else ("Discover", "discover")
    ]

    nombre_medio = _site_display_name(site_url) or "sitio"
    title = f"{nombre_medio} - Análisis de impacto de Core Update - {date.today()}"

    template_id = _template_id("core_update", st.session_state.get("oauth", {}).get("account"))
    if not template_id:
        st.error("No se configuró el ID de template para 'core_update' en st.secrets.")
        st.stop()

    sh, sid = copy_template_and_open(drive, gsclient, template_id, title, dest_folder_id)
    st.caption(f"Períodos calculados → Pre: {pre_ini} a {pre_fin} | Post: {post_ini} a {post_fin}")

    wrote_any = False
    for tipo_nombre, tipo_val in tipos:
        # --- PRE / POST (por página) con columnas solicitadas ---
        df_pre = consultar_datos(sc_service, site_url, pre_ini, pre_fin, tipo_val, pais=pais, seccion_filtro=seccion)
        df_post = consultar_datos(sc_service, site_url, post_ini, post_fin, tipo_val, pais=pais, seccion_filtro=seccion)
        debug_log(f"[{tipo_nombre}] filas PRE", len(df_pre))
        debug_log(f"[{tipo_nombre}] filas POST", len(df_post))

        if not df_pre.empty:
            ws_pre = _ensure_ws(sh, f"{tipo_nombre} | Pre Core Update")
            safe_set_df(ws_pre, _spanish_columns_prepost(df_pre))
            wrote_any = True
        else:
            st.info(f"Sin datos PRE para {tipo_nombre} con esos filtros.")

        if not df_post.empty:
            ws_post = _ensure_ws(sh, f"{tipo_nombre} | Post Core Update")
            safe_set_df(ws_post, _spanish_columns_prepost(df_post))
            wrote_any = True
        else:
            st.info(f"Sin datos POST para {tipo_nombre} con esos filtros.")

        # --- Tráfico por país (apoyo) ---
        df_pre_p = consultar_por_pais(sc_service, site_url, pre_ini, pre_fin, tipo_val, seccion_filtro=seccion)
        df_post_p = consultar_por_pais(sc_service, site_url, post_ini, post_fin, tipo_val, seccion_filtro=seccion)
        dfp = df_pre_p.merge(df_post_p, on="country", how="outer", suffixes=("_pre", "_post")).fillna(0)
        if not dfp.empty:
            safe_set_df(_ensure_ws(sh, f"{tipo_nombre} | Tráfico por país"), dfp)

        # --- Datos Diarios agregados por fecha (PRE+POST juntos) ---
        df_daily_all = pd.concat([
            fetch_gsc_daily_by_page(sc_service, site_url, pre_ini,  pre_fin,  tipo=tipo_val, country_iso3=pais, section_path=seccion),
            fetch_gsc_daily_by_page(sc_service, site_url, post_ini, post_fin, tipo=tipo_val, country_iso3=pais, section_path=seccion),
        ], ignore_index=True) if True else pd.DataFrame()

        debug_log(f"[{tipo_nombre}] Datos Diarios concatenado (filas)", len(df_daily_all))
        if not df_daily_all.empty:
            df_daily_out = _spanish_columns_daily(df_daily_all)
            ws_daily = _ensure_ws(sh, f"{tipo_nombre} | Datos Diarios")
            safe_set_df(ws_daily, df_daily_out)
            wrote_any = True
        else:
            st.info(f"Sin datos DIARIOS para {tipo_nombre} con esos filtros.")

    # Config
    cfg = pd.DataFrame([
        ("Sitio Analizado", site_url),
        ("Tipo de análisis", ", ".join([t[0] for t in tipos])),
        ("Periodo Pre Core Update", f"{pre_ini} a {pre_fin}"),
        ("Periodo Post Core Update", f"{post_ini} a {post_fin}"),
        ("Sección", seccion or "Todo el sitio"),
        ("País", pais or "Todos"),
    ], columns=["Configuración", "Valor"])
    safe_set_df(_ensure_ws(sh, "Configuracion"), cfg)

    if not wrote_any:
        st.warning("No se exportó ninguna hoja con datos. Revisá propiedad/fechas/tipo/filtros.")
    return sid
