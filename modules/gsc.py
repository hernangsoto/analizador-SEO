# modules/gsc.py
from __future__ import annotations

from typing import Iterable, Tuple, Optional
import pandas as pd
import streamlit as st
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .utils import debug_log


# ========= Cliente SC =========

def ensure_sc_client(creds):
    return build("searchconsole", "v1", credentials=creds)


# ========= Helpers de consulta =========

def _fetch_all_rows(service, site_url, body, page_size=25000):
    """Paginación segura con manejo de errores."""
    all_rows, start = [], 0
    while True:
        try:
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
        except HttpError as e:
            debug_log("HttpError en Search Console", str(e))
            break
        except Exception as e:
            debug_log("Error en Search Console", str(e))
            break
    return all_rows


def consultar_datos(service, site_url, fecha_inicio, fecha_fin, tipo_dato, pais=None, seccion_filtro=None):
    """Devuelve métricas por página para el rango dado."""
    seccion_frag = seccion_filtro.strip("/") if seccion_filtro else None
    body = {"startDate": str(fecha_inicio), "endDate": str(fecha_fin), "dimensions": ["page"]}
    body["type"] = "discover" if tipo_dato == "discover" else "web"
    if tipo_dato == "discover":
        body["dataState"] = "all"

    filters = []
    if seccion_frag:
        filters.append({"dimension": "page", "operator": "contains", "expression": f"/{seccion_frag}"})
    if pais:
        filters.append({"dimension": "country", "operator": "equals", "expression": pais})
    if filters:
        body["dimensionFilterGroups"] = [{"filters": filters}]

    rows = _fetch_all_rows(service, site_url, body)
    if not rows:
        return pd.DataFrame(columns=["url", "clicks", "impressions", "ctr", "position"])
    df = pd.DataFrame([
        {
            "url": r["keys"][0],
            "clicks": r.get("clicks", 0),
            "impressions": r.get("impressions", 0),
            "ctr": r.get("ctr", 0.0),
            "position": r.get("position", 0.0),
        }
        for r in rows
    ])
    return df


def consultar_por_pais(service, site_url, fecha_inicio, fecha_fin, tipo_dato, seccion_filtro=None):
    """Clicks/Impressions por país en el rango dado (agregado)."""
    seccion_frag = seccion_filtro.strip("/") if seccion_filtro else None
    body = {"startDate": str(fecha_inicio), "endDate": str(fecha_fin), "dimensions": ["country"]}
    body["type"] = "discover" if tipo_dato == "discover" else "web"
    if tipo_dato == "discover":
        body["dataState"] = "all"

    filters = []
    if seccion_frag:
        filters.append({"dimension": "page", "operator": "contains", "expression": f"/{seccion_frag}"})
    if filters:
        body["dimensionFilterGroups"] = [{"filters": filters}]

    rows = _fetch_all_rows(service, site_url, body, page_size=250)
    if not rows:
        return pd.DataFrame(columns=["country", "clicks", "impressions"])
    df = pd.DataFrame([
        {"country": r.get("keys", [None])[0], "clicks": r.get("clicks", 0), "impressions": r.get("impressions", 0)}
        for r in rows
    ])
    return df.groupby("country", as_index=False)[["clicks", "impressions"]].sum().sort_values("clicks", ascending=False)


# ========= Evergreen helpers =========

def month_range(start_date, end_date):
    cur = pd.Timestamp(start_date).replace(day=1)
    endm = pd.Timestamp(end_date).replace(day=1)
    while cur <= endm:
        yield cur.date(), (cur + pd.offsets.MonthEnd(0)).date()
        cur = (cur + pd.offsets.MonthBegin(1))


def fetch_gsc_monthly_by_page(service, site_url, start_dt, end_dt, country_iso3=None, section_path=None):
    frames = []
    for m_start, m_end in month_range(start_dt, end_dt):
        body = {
            "startDate": str(m_start),
            "endDate": str(m_end),
            "dimensions": ["page"],
            "type": "web",
            "aggregationType": "auto",
        }
        filters = []
        if country_iso3:
            filters.append({"dimension": "country", "operator": "equals", "expression": country_iso3})
        if section_path:
            filters.append({"dimension": "page", "operator": "contains", "expression": section_path})
        if filters:
            body["dimensionFilterGroups"] = [{"filters": filters}]
        rows = _fetch_all_rows(service, site_url, body)
        if rows:
            df = pd.DataFrame([
                {
                    "page": r["keys"][0],
                    "month": pd.to_datetime(m_start),
                    "clicks": r.get("clicks", 0),
                    "impressions": r.get("impressions", 0),
                }
                for r in rows
            ])
            frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["page", "month", "clicks", "impressions"])


def fetch_site_daily_totals(service, site_url, start_dt, end_dt, country_iso3=None, section_path=None):
    body = {"startDate": str(start_dt), "endDate": str(end_dt), "dimensions": ["date"], "type": "web"}
    filters = []
    if country_iso3:
        filters.append({"dimension": "country", "operator": "equals", "expression": country_iso3})
    if section_path:
        filters.append({"dimension": "page", "operator": "contains", "expression": section_path})
    if filters:
        body["dimensionFilterGroups"] = [{"filters": filters}]
    rows = _fetch_all_rows(service, site_url, body, page_size=5000)
    df = pd.DataFrame([
        {"date": pd.to_datetime(r["keys"][0]).date(), "clicks": r.get("clicks", 0), "impressions": r.get("impressions", 0)}
        for r in rows
    ]) if rows else pd.DataFrame(columns=["date", "clicks", "impressions"])
    if not df.empty:
        df["ctr"] = (df["clicks"] / df["impressions"]).fillna(0)
    return df


def fetch_gsc_daily_evergreen(service, site_url, start_dt, end_dt, country_iso3=None, section_path=None, page_size=25000):
    rows_all, start_row = [], 0
    body = {
        "startDate": str(start_dt),
        "endDate": str(end_dt),
        "dimensions": ["page", "date"],
        "rowLimit": page_size,
        "type": "web",
        "aggregationType": "auto",
    }
    filters = []
    if country_iso3:
        filters.append({"dimension": "country", "operator": "equals", "expression": country_iso3})
    if section_path:
        filters.append({"dimension": "page", "operator": "contains", "expression": section_path})
    if filters:
        body["dimensionFilterGroups"] = [{"filters": filters}]
    while True:
        body["startRow"] = start_row
        try:
            resp = service.searchanalytics().query(siteUrl=site_url, body=body).execute()
        except HttpError as e:
            debug_log("HttpError diario Evergreen", str(e))
            break
        rows = resp.get("rows", [])
        if not rows:
            break
        for r in rows:
            rows_all.append({
                "page": r["keys"][0],
                "date": pd.to_datetime(r["keys"][1]),
                "clicks": r.get("clicks", 0),
                "impressions": r.get("impressions", 0),
                "ctr": r.get("ctr", 0.0),
                "position": r.get("position", 0.0),
            })
        if len(rows) < page_size:
            break
        start_row += page_size
    df = pd.DataFrame(rows_all)
    if not df.empty:
        df["date"] = df["date"].dt.date
    return df
