# modules/auditoria.py
from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import urlsplit

import pandas as pd
import streamlit as st


# Usamos los helpers de Drive/Sheets ya existentes en tu proyecto
from modules.drive import copy_template_and_open, safe_set_df, _ensure_ws


# ============ utilidades internas ============

def _get_template_id(kind: str = "auditoria", account_key: str | None = None) -> str | None:
    """
    Lee el template desde st.secrets["templates"].
    Prioriza por cuenta si existiera (no obligatorio). Si no, usa la clave global.
    """
    troot = st.secrets.get("templates", {})
    if account_key and isinstance(troot.get(account_key), dict):
        return troot[account_key].get(kind) or troot.get(kind)
    return troot.get(kind)


def _fetch_all_rows(service, site_url: str, body: dict, page_size: int = 25000):
    """
    Paginación para Search Console API.
    """
    all_rows, start = [], 0
    while True:
        page_body = dict(body)
        page_body["rowLimit"] = page_size
        if start:
            page_body["startRow"] = start
        resp = service.searchanalytics().query(siteUrl=site_url, body=page_body).execute()
        batch = resp.get("rows", [])
        if not batch:
            break
        all_rows.extend(batch)
        if len(batch) < page_size:
            break
        start += page_size
    return all_rows


def _add_filters(country_iso3: str | None, section_path: str | None):
    """
    Construye filtros para el body de GSC.
    """
    filters = []
    if country_iso3:
        filters.append({"dimension": "country", "operator": "equals", "expression": country_iso3})
    if section_path:
        # Si te interesa solo que contenga el path
        filters.append({"dimension": "page", "operator": "contains", "expression": section_path})
    return [{"filters": filters}] if filters else None


def _fetch_daily_totals(service, site_url, start_dt, end_dt, tipo: str, country_iso3=None, section_path=None):
    """
    Totales diarios para un rango (una sola consulta grande).
    tipo: "web" o "discover".
    """
    body = {"startDate": str(start_dt), "endDate": str(end_dt), "dimensions": ["date"], "type": tipo}
    if tipo == "discover":
        body["dataState"] = "all"
    dfg = _add_filters(country_iso3, section_path)
    if dfg:
        body["dimensionFilterGroups"] = dfg
    rows = _fetch_all_rows(service, site_url, body, page_size=5000)
    if not rows:
        return pd.DataFrame(columns=["date", "clicks", "impressions", "ctr"])
    df = pd.DataFrame(
        [{"date": pd.to_datetime(r["keys"][0]).date(),
          "clicks": r.get("clicks", 0),
          "impressions": r.get("impressions", 0),
          "ctr": r.get("ctr", 0.0)} for r in rows]
    )
    return df


def _path_section(url: str) -> str:
    """
    Devuelve la sección principal a partir del path: '/deportes/', '/vida/', etc.
    Si no hay, devuelve '/'.
    """
    try:
        p = urlsplit(url).path
        parts = [seg for seg in p.split("/") if seg]
        return f"/{parts[0]}/" if parts else "/"
    except Exception:
        return "/"


def _fetch_pages(service, site_url, start_dt, end_dt, tipo: str, country_iso3=None, section_path=None):
    """
    Métricas por página para un rango.
    Devuelve: url, clicks, impressions, ctr, position, sección
    """
    body = {"startDate": str(start_dt), "endDate": str(end_dt), "dimensions": ["page"], "type": tipo}
    if tipo == "discover":
        body["dataState"] = "all"
    dfg = _add_filters(country_iso3, section_path)
    if dfg:
        body["dimensionFilterGroups"] = dfg
    rows = _fetch_all_rows(service, site_url, body, page_size=25000)
    if not rows:
        return pd.DataFrame(columns=["url", "clicks", "impressions", "ctr", "position", "sección"])
    df = pd.DataFrame(
        [{"url": r["keys"][0],
          "clicks": r.get("clicks", 0),
          "impressions": r.get("impressions", 0),
          "ctr": r.get("ctr", 0.0),
          "position": r.get("position", 0.0)} for r in rows]
    )
    df["sección"] = df["url"].map(_path_section)
    return df


def _period_days_from_freq(freq: str, custom_days: int | None) -> int:
    """
    Semanal → 7, Quincenal → 15, Mensual → 30 (aprox), Personalizado → custom_days.
    """
    if freq == "Semanal":
        return 7
    if freq == "Quincenal":
        return 15
    if freq == "Mensual":
        return 30
    # Personalizado
    return max(1, int(custom_days or 7))


def _build_periods(freq: str, custom_days: int | None, lag_days: int, num_previos: int):
    """
    Construye lista de periodos: 1 periodo de auditoría + N previos.
    Cada periodo es un dict: {"label": str, "start": date, "end": date}
    """
    days = _period_days_from_freq(freq, custom_days)
    today_util = date.today() - timedelta(days=lag_days)

    # Periodo de auditoría (el "actual" terminando hoy_util)
    cur_end = today_util
    cur_start = cur_end - timedelta(days=days - 1)

    periods = [{"label": "Periodo de auditoría", "start": cur_start, "end": cur_end}]

    # Agregar previos hacia atrás
    prev_end = cur_start - timedelta(days=1)
    for i in range(1, num_previos + 1):
        prev_start = prev_end - timedelta(days=days - 1)
        periods.append({"label": f"Periodo previo {i}", "start": prev_start, "end": prev_end})
        prev_end = prev_start - timedelta(days=1)

    # Del más reciente al más antiguo está bien; si querés el inverso, usa periods[::-1]
    return periods, days


def _combined_bounds(periods):
    """
    Devuelve (min_start, max_end) para consultar los diarios en una sola llamada.
    """
    starts = [p["start"] for p in periods]
    ends = [p["end"] for p in periods]
    return min(starts), max(ends)


def _period_totals_from_daily(daily_df: pd.DataFrame, pstart: date, pend: date):
    """
    Suma clicks/impresiones por el sub-rango [pstart, pend] a partir del df diario global.
    """
    if daily_df.empty:
        return 0, 0, 0.0
    mask = (daily_df["date"] >= pstart) & (daily_df["date"] <= pend)
    sub = daily_df.loc[mask]
    clicks = int(sub["clicks"].sum()) if not sub.empty else 0
    impressions = int(sub["impressions"].sum()) if not sub.empty else 0
    ctr = (clicks / impressions) if impressions else 0.0
    return clicks, impressions, ctr


# ============ función principal ============

def run_auditoria(sc_service, drive, gsclient, site_url: str, params: dict, dest_folder_id: str | None = None) -> str:
    """
    Ejecuta la Auditoría de tráfico y escribe resultados en una copia del template.
    params:
      - frecuencia: "Semanal" | "Quincenal" | "Mensual" | "Personalizado"
      - custom_days: int | None
      - tipo_datos: "Search" | "Discover" | "Ambos"
      - seccion: path o None
      - pais: ISO-3 o None (Global)
      - num_previos: int (p.ej. 4)
      - lag_days: int
    """
    frecuencia   = params.get("frecuencia", "Semanal")
    custom_days  = params.get("custom_days")
    tipo_datos   = params.get("tipo_datos", "Ambos")
    seccion      = params.get("seccion")
    pais         = params.get("pais")
    num_previos  = int(params.get("num_previos", 4))
    lag_days     = int(params.get("lag_days", 3))

    # Armar periodos
    periods, period_days = _build_periods(frecuencia, custom_days, lag_days, num_previos)
    all_start, all_end = _combined_bounds(periods)

    # Preparar título y template
    dom = urlsplit(site_url).netloc.replace("www.", "")
    title = f"{dom} - Auditoría de tráfico - {date.today()}"

    # Lee template de st.secrets
    template_id = _get_template_id("auditoria", account_key=None)
    if not template_id:
        st.error("No se configuró el ID de template para 'auditoria' en st.secrets['templates'].")
        st.stop()

    # Copiar template y abrir
    sh, sid = copy_template_and_open(drive, gsclient, template_id, title, dest_folder_id)

    # Determinar tipos a correr
    tipos = []
    if tipo_datos in ("Search", "Ambos"):
        tipos.append(("Search", "web"))
    if tipo_datos in ("Discover", "Ambos"):
        tipos.append(("Discover", "discover"))

    for tipo_nombre, tipo_val in tipos:
        # 1) Totales diarios (una sola llamada global)
        daily = _fetch_daily_totals(sc_service, site_url, all_start, all_end, tipo_val, country_iso3=pais, section_path=seccion)
        daily_out = daily.rename(columns={"date": "fecha", "clicks": "clics", "impressions": "impresiones"})
        if not daily_out.empty:
            daily_out["ctr"] = (daily_out["clics"] / daily_out["impresiones"]).fillna(0)
        ws_daily = _ensure_ws(sh, f"{tipo_nombre} | Datos Diarios")
        safe_set_df(ws_daily, daily_out[["fecha", "clics", "impresiones", "ctr"]] if not daily_out.empty else daily_out)

        # 2) Totales por período (se derivan del df diario global)
        rows_tot = []
        for p in periods:
            c, i, ctr = _period_totals_from_daily(daily, p["start"], p["end"])
            rows_tot.append({
                "periodo": p["label"],
                "inicio": p["start"],
                "fin": p["end"],
                "clics": c,
                "impresiones": i,
                "ctr": ctr,
            })
        df_tot = pd.DataFrame(rows_tot)
        ws_tot = _ensure_ws(sh, f"{tipo_nombre} | Totales por período")
        safe_set_df(ws_tot, df_tot)

        # 3) Páginas por período (una consulta por período)
        frames = []
        for p in periods:
            dfp = _fetch_pages(sc_service, site_url, p["start"], p["end"], tipo_val, country_iso3=pais, section_path=seccion)
            if not dfp.empty:
                dfp = dfp.rename(columns={"impressions": "impresiones", "clicks": "clics", "position": "posición"})
                dfp["periodo"] = p["label"]
                frames.append(dfp[["periodo", "url", "clics", "impresiones", "ctr", "posición", "sección"]])
        pages_all = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
            columns=["periodo", "url", "clics", "impresiones", "ctr", "posición", "sección"]
        )
        ws_pages = _ensure_ws(sh, f"{tipo_nombre} | Páginas por período")
        safe_set_df(ws_pages, pages_all)

    # 4) Hoja de Configuración / metadatos
    cfg_rows = [
        ("Sitio Analizado", site_url),
        ("Frecuencia", frecuencia),
        ("Días por período", period_days),
        ("# Períodos previos", num_previos),
        ("Lag de datos", lag_days),
        ("Tipos de datos", ", ".join([t[0] for t in tipos]) if tipos else tipo_datos),
        ("Sección", seccion or "Todo el sitio"),
        ("País", pais or "Global"),
        ("Rango total (diario)", f"{all_start} a {all_end}"),
    ]
    cfg = pd.DataFrame(cfg_rows, columns=["Configuración", "Valor"])
    ws_cfg = _ensure_ws(sh, "Configuracion")
    safe_set_df(ws_cfg, cfg)

    return sid
