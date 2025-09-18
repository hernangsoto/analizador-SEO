# modules/app_ext.py
"""
Capa de compatibilidad para cargar analizadores desde el paquete externo
`seo_analisis_ext` (repo privado), con fallbacks locales.

Exporta:
- USING_EXT, EXT_PACKAGE
- run_core_update, run_evergreen, run_traffic_audit, run_names_analysis
- run_discover_snoop, run_content_analysis
- run_content_structure
- run_sections_analysis
- run_report_results
- run_ga4_audience_report
- run_discover_retention, DiscoverRetentionParams  <-- NUEVO

Incluye:
- Shim robusto para run_content_analysis (normaliza fechas, tipo, filtros y alias)
- Shim de normalización para run_content_structure (fechas, source, filtros, scraping)
- Runner Discover con wrapper:
    * Usa el paquete externo si está disponible.
    * Si la API rechaza HOUR/HOURLY_ALL (HTTP 400 INVALID_ARGUMENT), cae a modo Diario.
    * En modo Diario muestra un aviso en la UI (Streamlit) y arma el Sheets con el template.
- Parche de serialización segura al escribir DataFrames a Google Sheets
"""

# ============================================================================ #
# 1) Carga "suave" del paquete externo (sin depender de modules.utils)        #
# ============================================================================ #
try:
    import importlib  # lo usamos más abajo para parches opcionales
except Exception:
    importlib = None  # type: ignore

try:
    import seo_analisis_ext as _ext  # type: ignore
except Exception:
    _ext = None  # type: ignore

def _get_ext_attr(name: str, default=None):
    return getattr(_ext, name, default) if _ext is not None else default

# =================== Preferimos funciones del paquete externo ===================

run_core_update         = _get_ext_attr("run_core_update")
run_evergreen           = _get_ext_attr("run_evergreen")
run_traffic_audit       = _get_ext_attr("run_traffic_audit")
run_names_analysis      = _get_ext_attr("run_names_analysis")
run_discover_snoop      = _get_ext_attr("run_discover_snoop")
run_content_analysis    = _get_ext_attr("run_content_analysis")
run_content_structure   = _get_ext_attr("run_content_structure")
run_sections_analysis   = _get_ext_attr("run_sections_analysis")
run_report_results      = _get_ext_attr("run_report_results")
run_ga4_audience_report = _get_ext_attr("run_ga4_audience_report")
# Nuevo análisis Discover Retention (preferir export desde __init__, si existe)
run_discover_retention  = _get_ext_attr("run_discover_retention")
DiscoverRetentionParams = _get_ext_attr("DiscoverRetentionParams")

# ============================= Fallbacks ========================================

# Core / Evergreen (local)
if (run_core_update is None) or (run_evergreen is None):
    try:
        from modules.analysis import run_core_update as _rcu, run_evergreen as _rev  # type: ignore
        run_core_update = run_core_update or _rcu
        run_evergreen   = run_evergreen   or _rev
    except Exception:
        pass

# Auditoría de tráfico (local)
if run_traffic_audit is None:
    try:
        from modules.analysis import run_traffic_audit as _rta  # type: ignore
        run_traffic_audit = _rta
    except Exception:
        pass

# Nombres (ext → local)
if run_names_analysis is None:
    try:
        from seo_analisis_ext.analysis_names import run_names_analysis as _rna  # type: ignore
        run_names_analysis = _rna
    except Exception:
        try:
            from modules.analysis_names import run_names_analysis as _rna  # type: ignore
            run_names_analysis = _rna
        except Exception:
            run_names_analysis = None

# Discover Snoop (ext → local opcional)
if run_discover_snoop is None:
    _rds = None
    try:
        from seo_analisis_ext.discover_snoop import run_discover_snoop as _rds  # type: ignore
    except Exception:
        try:
            # Fallback local opcional:
            # from modules.discover_snoop import run_discover_snoop as _rds  # type: ignore
            _rds = None
        except Exception:
            _rds = None
    run_discover_snoop = _rds

# Content Analysis (ext rutas alternas → local)
if run_content_analysis is None:
    _rca = None
    try:
        from seo_analisis_ext.content_analysis import run_content_analysis as _rca  # type: ignore
    except Exception:
        try:
            from seo_analisis_ext.analysis_content import run_content_analysis as _rca  # type: ignore
        except Exception:
            try:
                from seo_analisis_ext.content import run_content_analysis as _rca  # type: ignore
            except Exception:
                _rca = None
    if _rca is None:
        try:
            from modules.content_analysis import run_content_analysis as _rca  # type: ignore
        except Exception:
            try:
                from modules.analysis_content import run_content_analysis as _rca  # type: ignore
            except Exception:
                _rca = None
    run_content_analysis = _rca

# Content Structure (ext rutas alternas → local opcional)
if run_content_structure is None:
    _rcs = None
    try:
        from seo_analisis_ext.content_structure import run_content_structure as _rcs  # type: ignore
    except Exception:
        try:
            from seo_analisis_ext.analysis_structure import run_content_structure as _rcs  # type: ignore
        except Exception:
            try:
                # Fallbacks locales opcionales:
                # from modules.content_structure import run_content_structure as _rcs  # type: ignore
                _rcs = None
            except Exception:
                _rcs = None
    run_content_structure = _rcs

# Sections Analysis (ext → local opcional)
if run_sections_analysis is None:
    _rsa = None
    try:
        from seo_analisis_ext.sections_analysis import run_sections_analysis as _rsa  # type: ignore
    except Exception:
        try:
            # Fallback local opcional:
            # from modules.sections_analysis import run_sections_analysis as _rsa  # type: ignore
            _rsa = None
        except Exception:
            _rsa = None
    run_sections_analysis = _rsa

# Reporte de resultados (si no está expuesto en __init__, buscar submódulo; si no existe, fallback local)
if run_report_results is None:
    try:
        from seo_analisis_ext.report_results import run_report_results as _rrr  # type: ignore
        run_report_results = _rrr
    except Exception:
        # --------------------------
        # Fallback local "simple" GSC
        # --------------------------
        from datetime import date
        import pandas as _pd  # local alias
        from urllib.parse import urlsplit as _urlsplit

        def _rr__domain_from_site(site_url: str) -> str:
            try:
                u = site_url.strip()
                if not u:
                    return "sitio"
                if "://" not in u:
                    u = "https://" + u
                net = _urlsplit(u).netloc
                return net or u.strip("/").replace("https://", "").replace("http://", "")
            except Exception:
                return site_url.replace("https://", "").replace("http://", "").strip("/")

        def _rr__as_date(d):
            if isinstance(d, date):
                return d
            s = str(d).strip()
            y, m, dd = int(s[0:4]), int(s[5:7]), int(s[8:10])
            return date(y, m, dd)

        def _rr__gsc_query(sc, site, start, end, search_type, dimensions, filters=None,
                           row_limit=25000, order_by=None):
            body = {
                "startDate": str(start),
                "endDate": str(end),
                "dimensions": dimensions,
                "rowLimit": int(row_limit),
                "startRow": 0,
                "type": "discover" if search_type == "discover" else "web",
            }
            if filters:
                body["dimensionFilterGroups"] = [{"groupType": "and", "filters": filters}]
            if order_by:
                body["orderBy"] = order_by

            resp = sc.searchanalytics().query(siteUrl=site, body=body).execute()
            rows = resp.get("rows", []) or []
            out = []
            for r in rows:
                keys = r.get("keys") or []
                rec = {}
                for i, dim in enumerate(dimensions):
                    val = keys[i] if i < len(keys) else ""
                    if dim == "date" and val and len(val) == 10:
                        rec["date"] = val
                    else:
                        rec[dim] = val
                rec["clicks"] = r.get("clicks", 0)
                rec["impressions"] = r.get("impressions", 0)
                rec["ctr"] = r.get("ctr", 0.0)
                rec["position"] = r.get("position", 0.0)
                out.append(rec)
            return _pd.DataFrame(out)

        def _rr__apply_metrics(df: _pd.DataFrame, metrics: dict) -> _pd.DataFrame:
            if df is None or df.empty:
                return df
            if "ctr" in df.columns:
                df["CTR"] = (df["ctr"].fillna(0) * 100).round(2)
            if "position" in df.columns:
                df["Posición"] = df["position"].astype(float).round(2)
            keep = []
            if metrics.get("clicks"):      keep.append("Clics")
            if metrics.get("impressions"): keep.append("Impresiones")
            if metrics.get("ctr"):         keep.append("CTR")
            if metrics.get("position"):    keep.append("Posición")
            if "clicks" in df.columns and "Clics" not in df.columns:
                df["Clics"] = df["clicks"]
            if "impressions" in df.columns and "Impresiones" not in df.columns:
                df["Impresiones"] = df["impressions"]
            dims = [c for c in df.columns if c in ("date", "page", "country")]
            ordered = dims + [c for c in ["Clics", "Impresiones", "CTR", "Posición"] if c in keep]
            return df[ordered] if ordered else df

        def _rr__write_ws(ws, df: _pd.DataFrame, empty_note="(sin datos)"):
            if df is None or df.empty:
                ws.clear()
                ws.update([[empty_note]])
                return
            ws.clear()
            ws.update([df.columns.tolist()] + df.fillna("").astype(str).values.tolist())

        def run_report_results(sc_service, drive_service, gs_client, site_url: str, params: dict, dest_folder_id: str | None = None) -> str | None:  # type: ignore[override]
            start = _rr__as_date(params.get("start"))
            end   = _rr__as_date(params.get("end"))
            origin = (params.get("origin") or "search").strip().lower()
            origin_list = ["search", "discover"] if origin == "both" else [origin]
            path = params.get("path") or None
            countries = list(params.get("countries") or [])
            metrics = dict(params.get("metrics") or {"clicks": True, "impressions": True, "ctr": True, "position": origin != "discover"})
            top_n = int(params.get("top_n", 20))
            title_prefix = params.get("sheet_title_prefix") or "Reporte de resultados"

            site_pretty = _rr__domain_from_site(site_url)
            sheet_name = f"{title_prefix} ({start} a {end}) - {site_pretty}"
            meta = {"name": sheet_name, "mimeType": "application/vnd.google-apps.spreadsheet"}
            if dest_folder_id:
                meta["parents"] = [dest_folder_id]
            newfile = drive_service.files().create(body=meta, fields="id,name,webViewLink").execute()
            sid = newfile["id"]

            sh = gs_client.open_by_key(sid)
            ws0 = sh.sheet1
            ws0.update_title("Resumen")

            def _ensure(title: str):
                try:
                    return sh.worksheet(title)
                except Exception:
                    return sh.add_worksheet(title=title, rows=100, cols=20)

            for src in origin_list:
                label = "Search" if src == "search" else "Discover"
                filters = []
                if path:
                    filters.append({"dimension": "page", "operator": "contains", "expression": path})
                try:
                    df_series = _rr__gsc_query(
                        sc_service, site_url, start, end, src,
                        dimensions=["date"], filters=filters, row_limit=25000
                    )
                    if not df_series.empty:
                        df_series = df_series.groupby(["date"], as_index=False).agg({
                            "clicks": "sum", "impressions": "sum", "ctr": "mean", "position": "mean"
                        })
                        df_series = _rr__apply_metrics(df_series, metrics)
                except Exception:
                    import pandas as _pd
                    df_series = _pd.DataFrame()
                _rr__write_ws(_ensure(f"Serie diaria ({label})"), df_series)

                try:
                    df_top_global = _rr__gsc_query(
                        sc_service, site_url, start, end, src,
                        dimensions=["page"],
                        filters=filters,
                        row_limit=max(1000, top_n if top_n > 0 else 1000),
                        order_by=[{"field": "clicks", "descending": True}],
                    )
                    if not df_top_global.empty:
                        df_top_global = (df_top_global
                                         .sort_values("clicks", ascending=False)
                                         .groupby("page", as_index=False)
                                         .first())
                        if top_n > 0:
                            df_top_global = df_top_global.head(top_n)
                        df_top_global = _rr__apply_metrics(df_top_global, metrics)
                        df_top_global = df_top_global.rename(columns={"page": "URL"})
                except Exception:
                    import pandas as _pd
                    df_top_global = _pd.DataFrame()
                _rr__write_ws(_ensure(f"Top Global ({label})"), df_top_global)

                for iso3 in countries:
                    iso = str(iso3).strip().lower()
                    filters_iso = list(filters) if filters else []
                    filters_iso.append({"dimension": "country", "operator": "equals", "expression": iso})
                    try:
                        df_top_ctry = _rr__gsc_query(
                            sc_service, site_url, start, end, src,
                            dimensions=["page", "country"],
                            filters=filters_iso,
                            row_limit=max(1000, top_n if top_n > 0 else 1000),
                            order_by=[{"field": "clicks", "descending": True}],
                        )
                        if not df_top_ctry.empty:
                            df_top_ctry = (df_top_ctry
                                           .sort_values("clicks", ascending=False)
                                           .groupby("page", as_index=False)
                                           .first())
                            if top_n > 0:
                                df_top_ctry = df_top_ctry.head(top_n)
                            df_top_ctry = _rr__apply_metrics(df_top_ctry, metrics)
                            df_top_ctry = df_top_ctry.rename(columns={"page": "URL", "country": "País"})
                    except Exception:
                        import pandas as _pd
                        df_top_ctry = _pd.DataFrame()
                    _rr__write_ws(_ensure(f"Top {iso.upper()} ({label})"), df_top_ctry)

            try:
                import pandas as _pd
                ws_meta = _ensure("Meta")
                info = _pd.DataFrame({
                    "campo": ["site_url", "start", "end", "origin", "path", "countries", "top_n", "metrics"],
                    "valor": [
                        site_url, str(start), str(end),
                        origin, path or "(todo el sitio)",
                        ", ".join([c.upper() for c in countries]) if countries else "(Global)",
                        top_n,
                        ", ".join([k for k, v in metrics.items() if v]) or "(ninguna)"
                    ],
                })
                _rr__write_ws(ws_meta, info)
            except Exception:
                pass

            return sid

# GA4 Audiencia (ext → submódulo → local)
if run_ga4_audience_report is None:
    _ga4aud = None
    try:
        from seo_analisis_ext.ga4_audience import run_ga4_audience_report as _ga4aud  # type: ignore
    except Exception:
        pass
    if _ga4aud is None:
        try:
            from modules.ga4_audience import run_ga4_audience_report as _ga4aud  # type: ignore
        except Exception:
            _ga4aud = None
    run_ga4_audience_report = _ga4aud

# =============================================================================
# Discover Retention (ext → submódulo → local opcional) con wrapper y fallback
# =============================================================================

def _resolve_discover_retention():
    """Devuelve (fn, Params) intentando, en orden:
       1) Atributos exportados por el paquete externo ya cargado (_ext)
       2) Submódulo del paquete externo
       3) Fallback local modules/discover_retention
    """
    fn = getattr(_ext, "run_discover_retention", None) if _ext else None
    Params = getattr(_ext, "DiscoverRetentionParams", None) if _ext else None

    if fn is None or Params is None:
        try:
            from seo_analisis_ext.discover_retention import (  # type: ignore
                run_discover_retention as _fn2,
                DiscoverRetentionParams as _Params2,
            )
            fn = fn or _fn2
            Params = Params or _Params2
        except Exception:
            pass

    if fn is None or Params is None:
        try:
            from modules.discover_retention import (  # type: ignore
                run_discover_retention as _fn3,
                DiscoverRetentionParams as _Params3,
            )
            fn = fn or _fn3
            Params = Params or _Params3
        except Exception:
            pass

    return fn, Params

# ---- Helpers Discover (nombres/fechas/Sheets/GSC) ----------------------------

from typing import Any, Optional, Tuple, Dict, List

def _dr_try_import_streamlit():
    try:
        import streamlit as st  # type: ignore
    except Exception:
        st = None
    return st

def _dr_iso(d) -> str:
    from datetime import date, datetime
    if isinstance(d, (date, datetime)):
        return d.strftime("%Y-%m-%d")
    s = str(d).strip()
    return s[:10]

def _dr_to_date(d):
    from datetime import date, datetime
    if isinstance(d, date):
        return d
    s = str(d).strip()
    return date(int(s[0:4]), int(s[5:7]), int(s[8:10]))

def _dr_domain(site_url: str) -> str:
    try:
        from urllib.parse import urlsplit
        u = site_url.strip()
        if "://" not in u:
            u = "https://" + u
        return urlsplit(u).netloc or u.strip("/").replace("https://", "").replace("http://", "")
    except Exception:
        return site_url.replace("https://", "").replace("http://", "").strip("/")

def _dr_extract_section(url: str) -> str:
    try:
        from urllib.parse import urlsplit
        p = urlsplit(url).path
        if not p or p == "/":
            return ""
        seg = p.lstrip("/").split("/", 1)[0]
        return seg or ""
    except Exception:
        return ""

def _dr_drive_copy_from_template(drive_service, template_id: str, title: str, dest_folder_id: Optional[str]) -> str:
    body = {"name": title}
    if dest_folder_id:
        body["parents"] = [dest_folder_id]
    newfile = drive_service.files().copy(fileId=template_id, body=body, fields="id,name").execute()
    return newfile["id"]

def _dr_ws_ensure(sh, title: str):
    try:
        return sh.worksheet(title)
    except Exception:
        return sh.add_worksheet(title=title, rows=500, cols=26)

def _dr_write_ws(ws, values_or_df):
    try:
        import pandas as pd  # type: ignore
        if isinstance(values_or_df, pd.DataFrame):
            ws.clear()
            ws.update([values_or_df.columns.tolist()] + values_or_df.fillna("").astype(str).values.tolist())
            return
    except Exception:
        pass
    if isinstance(values_or_df, list) and values_or_df and isinstance(values_or_df[0], list):
        ws.clear()
        ws.update(values_or_df)
    else:
        ws.clear()
        ws.update([[str(values_or_df)]])

def _dr_gsc_query(sc, site, body: Dict[str, Any]) -> Dict[str, Any]:
    return sc.searchanalytics().query(siteUrl=site, body=body).execute()

def _dr_is_invalid_argument(err: Exception) -> bool:
    txt = str(err).lower()
    if "invalid_argument" in txt or "badrequest" in txt:
        return True
    if "hour" in txt or "hourly_all" in txt:
        return True
    try:
        # googleapiclient.errors.HttpError?
        from googleapiclient.errors import HttpError  # type: ignore
        if isinstance(err, HttpError) and getattr(err, "status_code", 400) == 400:
            return True
    except Exception:
        pass
    return False

def _normalize_params_for_ext(params: Dict[str, Any]) -> Dict[str, Any]:
    """Asegura que start/end estén listos y mapea alias frecuentes."""
    p = dict(params or {})
    win = dict(p.get("window") or {})
    per = dict(p.get("period") or {})
    start = p.get("start") or win.get("start") or per.get("start") or p.get("start_date") or p.get("fecha_inicio")
    end   = p.get("end")   or win.get("end")   or per.get("end")   or p.get("end_date")   or p.get("fecha_fin")
    if start: p["start"] = _dr_iso(start)
    if end:   p["end"]   = _dr_iso(end)
    if "window" in p:
        p.pop("window", None)  # algunos Param dataclass no lo aceptan
    # alias básicos
    if "origin" not in p and "source" in p:
        p["origin"] = p["source"]
    if "source" not in p and "origin" in p:
        p["source"] = p["origin"]
    if not p.get("origin"):
        p["origin"] = "discover"
    # template opcional
    if "template_id" not in p and "templateId" in p:
        p["template_id"] = p["templateId"]
    return p

# ---- Fallback Diario (con aviso en UI) ---------------------------------------

def _run_discover_retention_daily_compat(
    sc_service,
    drive_service,
    gs_client,
    site_url: str,
    params: Dict[str, Any],
    dest_folder_id: Optional[str] = None,
) -> Optional[str]:
    """
    Modo compatibilidad (sin HOUR/HOURLY_ALL):
      - Arma Sheets desde template
      - Completa Configuración
      - Extrae serie diaria Discover por URL para el período/segmentos y calcula:
          * Clics del período, Impresiones del período
          * Sección (primer segmento del path)
          * Fecha 1ra aparición (día), Fecha última aparición (día)
          * Días de permanencia (last - first)
          * Última visualización (día; hora vacía si no hay horas)
          * Status
      - NUEVO: Scraping concurrente de cada URL para extraer Fecha/Hora de publicación
               desde <meta property="article:published_time"> y/o JSON-LD (datePublished).
    """
    import pandas as pd  # type: ignore
    from datetime import date, datetime, timedelta
    import re, json, html
    from concurrent.futures import ThreadPoolExecutor, as_completed

    st = _dr_try_import_streamlit()

    # ---------------- Fechas del período ----------------
    start = params.get("start") or params.get("start_date")
    end   = params.get("end")   or params.get("end_date")
    if not start or not end:
        days = int(params.get("days") or params.get("period", {}).get("days") or 10)
        end_dt = date.today() - timedelta(days=1)
        start_dt = end_dt - timedelta(days=days-1)
    else:
        start_dt = _dr_to_date(start)
        end_dt   = _dr_to_date(end)
        if start_dt > end_dt:
            start_dt, end_dt = end_dt, start_dt

    # ---- AVISO en la UI si caemos a compatibilidad diaria ----
    if st is not None:
        key = f"_dr_hourly_fallback::{_dr_domain(site_url)}::{_dr_iso(start_dt)}::{_dr_iso(end_dt)}"
        if not st.session_state.get(key):
            st.session_state[key] = True
            st.warning(
                "⚠️ La propiedad de GSC aún no devuelve datos por **hora** para Discover vía API. "
                "Se ejecuta en **modo Diario (compatibilidad)** para continuar el análisis."
            )
            st.caption(
                "Se crea el Sheets con el template indicado; las columnas de **hora** de ingreso a Discover quedarán vacías. "
                "Cuando la API habilite horas para tu propiedad, el análisis volverá a usar granularidad horaria automáticamente."
            )

    # ---------------- Filtros GSC ----------------
    path_filter = params.get("path") or params.get("section") or None
    country = params.get("country") or params.get("pais") or params.get("filters", {}).get("country")
    if isinstance(country, str):
        country = country.strip().lower()
    else:
        country = None

    filters = []
    if path_filter:
        filters.append({"dimension": "page", "operator": "contains", "expression": str(path_filter)})
    if country:
        filters.append({"dimension": "country", "operator": "equals", "expression": country})

    # ---------------- Query GSC (Discover diario por URL) ----------------
    body = {
        "startDate": _dr_iso(start_dt),
        "endDate": _dr_iso(end_dt),
        "dimensions": ["date", "page"],
        "rowLimit": 25000,
        "type": "discover",
    }
    if filters:
        body["dimensionFilterGroups"] = [{"groupType": "and", "filters": filters}]
    resp = _dr_gsc_query(sc_service, site_url, body)
    rows = resp.get("rows", []) or []

    if not rows:
        # crear igual el sheets vacío para cumplir output
        return _dr_build_minimal_sheet(gs_client, drive_service, site_url, start_dt, end_dt, path_filter, country, dest_folder_id)

    df = pd.DataFrame([{
        "date": r["keys"][0],
        "url":  r["keys"][1],
        "clicks": r.get("clicks", 0),
        "impressions": r.get("impressions", 0),
    } for r in rows])

    df["date"] = pd.to_datetime(df["date"]).dt.date
    grp = df.groupby("url", as_index=False).agg(
        clicks=("clicks", "sum"),
        impressions=("impressions", "sum"),
        first_date=("date", "min"),
        last_date=("date", "max"),
    )
    grp["section"] = grp["url"].map(_dr_extract_section)
    grp["dias_perm"] = (grp["last_date"] - grp["first_date"]).dt.days

    # ---------------------------------------------------------------------
    #        NUEVO: Extracción de Fecha/Hora de publicación por URL
    # ---------------------------------------------------------------------
    # Estrategia:
    # 1) Buscar <meta property="article:published_time" content="...">
    # 2) Buscar variantes similares (name=..., og:article:published_time, pubdate, datePublished)
    # 3) Parsear JSON-LD y extraer "datePublished"
    # 4) Parseo flexible de fechas (dateutil si está, sino ISO/regex)
    #
    # Para performance: top-N URLs por clics (por defecto 300) con concurrencia.
    TOP_N = int(params.get("max_pubdate_fetch", 300))
    CONCURRENCY = int(params.get("pubdate_concurrency", 8))
    TIMEOUT = float(params.get("pubdate_timeout", 6.0))

    urls_ranked = grp.sort_values("clicks", ascending=False)["url"].tolist()
    urls_fetch = urls_ranked[:TOP_N]

    # --- HTTP GET con fallback a urllib ---
    def _fetch_html(url: str) -> str:
        # Primero, intentar con requests
        try:
            import requests  # type: ignore
            headers = {
                "User-Agent": params.get("ua") or "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            }
            r = requests.get(url, headers=headers, timeout=TIMEOUT, allow_redirects=True)
            enc = r.encoding or "utf-8"
            return r.text if r.text else r.content.decode(enc, errors="ignore")
        except Exception:
            # Fallback: urllib
            try:
                from urllib.request import Request, urlopen
                req = Request(
                    url,
                    headers={
                        "User-Agent": params.get("ua") or "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                    },
                )
                with urlopen(req, timeout=TIMEOUT) as resp2:
                    data = resp2.read()
                    try:
                        return data.decode("utf-8")
                    except Exception:
                        return data.decode("latin-1", errors="ignore")
            except Exception:
                return ""

    # --- Parseo de meta y JSON-LD ---
    META_RE = re.compile(
        r"""<meta\b[^>]*?(?:property|name)\s*=\s*["']([^"']+)["'][^>]*?
                (?:content|value)\s*=\s*["']([^"']+)["'][^>]*?>""",
        re.I | re.S | re.X,
    )
    SCRIPTS_JSONLD_RE = re.compile(
        r"""<script\b[^>]*?type\s*=\s*["']application/ld\+json["'][^>]*>(.*?)</script>""",
        re.I | re.S,
    )

    KEYS_PRIORIDAD = (
        "article:published_time",
        "og:article:published_time",
        "article:published",
        "datepublished",
        "pubdate",
        "publishdate",
        "dc.date", "dc.date.issued", "dc.date.published",
        "date",
    )

    def _try_parse_dt_str(s: str) -> Optional[datetime]:
        # Preferir dateutil si está
        try:
            from dateutil import parser as dp  # type: ignore
            return dp.parse(s)
        except Exception:
            pass
        # ISO básico
        s2 = s.strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(s2)
        except Exception:
            pass
        # yyyy-mm-dd hh:mm
        m = re.search(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})(?::\d{2})?", s)
        if m:
            try:
                return datetime.fromisoformat(m.group(1) + "T" + m.group(2))
            except Exception:
                return None
        # yyyy-mm-dd
        m = re.search(r"(\d{4}-\d{2}-\d{2})", s)
        if m:
            try:
                return datetime.fromisoformat(m.group(1))
            except Exception:
                return None
        return None

    def _extract_pub_dt_from_html(html_text: str) -> Tuple[str, str]:
        """Devuelve (fecha_str, hora_str) o ('','') si no encuentra."""
        if not html_text:
            return "", ""
        # --- meta tags ---
        metas = META_RE.findall(html_text)
        # pick by prioridad de clave
        cand_map = {}
        for k, v in metas:
            kk = (k or "").strip().lower()
            vv = html.unescape((v or "").strip())
            cand_map.setdefault(kk, []).append(vv)
        # buscar por orden de prioridad
        for key in KEYS_PRIORIDAD:
            vals = cand_map.get(key, [])
            for val in vals:
                dt = _try_parse_dt_str(val)
                if dt:
                    d = dt.date().isoformat()
                    h = dt.strftime("%H:%M")
                    return d, h

        # --- JSON-LD ---
        blocks = SCRIPTS_JSONLD_RE.findall(html_text) or []
        def _walk(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    lk = str(k).lower()
                    if lk == "datepublished" and isinstance(v, str):
                        dt = _try_parse_dt_str(v)
                        if dt:
                            return dt
                    # newsArticle a veces anida 'mainEntityOfPage' o 'article' con datePublished
                    if isinstance(v, (dict, list)):
                        got = _walk(v)
                        if got:
                            return got
            elif isinstance(o, list):
                for it in o:
                    got = _walk(it)
                    if got:
                        return got
            return None

        for raw in blocks:
            try:
                data = json.loads(html.unescape(raw))
            except Exception:
                # a veces vienen varios JSON pegados; intentar heurística
                try:
                    # extraer "datePublished":"..."
                    m = re.search(r'"datePublished"\s*:\s*"([^"]+)"', raw)
                    if m:
                        dt = _try_parse_dt_str(m.group(1))
                        if dt:
                            return dt.date().isoformat(), dt.strftime("%H:%M")
                except Exception:
                    pass
                continue
            dt = _walk(data)
            if dt:
                return dt.date().isoformat(), dt.strftime("%H:%M")

        return "", ""

    def _fetch_and_extract(url: str) -> Tuple[str, str, str]:
        try:
            html_text = _fetch_html(url)
            d, h = _extract_pub_dt_from_html(html_text)
            return url, d, h
        except Exception:
            return url, "", ""

    # Concurrencia controlada
    pub_date_map: Dict[str, Tuple[str, str]] = {}
    if urls_fetch:
        if st is not None:
            st.caption(f"⏳ Extrayendo fecha/hora de publicación desde meta/JSON-LD en {len(urls_fetch)} URLs (máx={TOP_N})…")
        with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
            futs = [ex.submit(_fetch_and_extract, u) for u in urls_fetch]
            ok = 0
            for f in as_completed(futs):
                u, d, h = f.result()
                if d:
                    ok += 1
                pub_date_map[u] = (d, h)
        if st is not None:
            st.caption(f"✅ Publicación detectada en {sum(1 for v in pub_date_map.values() if v[0])}/{len(urls_fetch)} URLs.")

    # Asignar a todas las URLs; las no pedidas/extraídas quedan en blanco
    grp["fecha_pub"] = grp["url"].map(lambda u: pub_date_map.get(u, ("", ""))[0])
    grp["hora_pub"]  = grp["url"].map(lambda u: pub_date_map.get(u, ("", ""))[1])

    # Hora ingreso Discover (sin hora en compat)
    grp["hora_ingreso"] = ""
    # Última visualización (día; hora vacía)
    grp["ultima_vis"] = grp["last_date"].astype(str)

    # ---------------- Status (usa fecha_pub si existe) ----------------
    s_start = start_dt
    s_end   = end_dt

    def _status_row(first_d, last_d, fecha_pub_str: str):
        pub_d = None
        if fecha_pub_str:
            try:
                pub_d = _dr_to_date(fecha_pub_str)
            except Exception:
                pub_d = None
        if pub_d and pub_d < s_start:
            return "Contenido publicado previo al análisis"
        if (first_d is not None) and (last_d is not None):
            if last_d == s_end:
                return "Contenido aún vigente"
            if (first_d >= s_start) and (last_d <= s_end):
                return "Contenido dentro del período de análisis"
        return "Revisar"

    grp["status"] = [ _status_row(fd, ld, fp) for fd, ld, fp in zip(grp["first_date"], grp["last_date"], grp["fecha_pub"]) ]

    # ---------------- Reordenar columnas para la hoja "Análisis" ----------------
    out = grp[[
        "url",               # A URL
        "clicks",            # B Clics del período
        "impressions",       # C Impresiones del período
        "section",           # D Sección
        "fecha_pub",         # E Fecha de publicación
        "hora_pub",          # F Hora de publicación
        "first_date",        # G Fecha de ingreso a Discover (día)
        "hora_ingreso",      # H Hora de ingreso a Discover (vacío)
        "dias_perm",         # I Días de permanencia
        "ultima_vis",        # J Última visualización en Discover (día)
        "status",            # K Status
    ]].rename(columns={
        "url": "URL",
        "clicks": "Clics del período",
        "impressions": "Impresiones del período",
        "section": "Sección",
        "fecha_pub": "Fecha de publicación",
        "hora_pub": "Hora de publicación",
        "first_date": "Fecha de ingreso a Discover",
        "hora_ingreso": "Hora de ingreso a Discover",
        "dias_perm": "Días de permanencia",
        "ultima_vis": "Última visualización en Discover",
        "status": "Status",
    })

    # ---------------- Crear Sheets desde template y volcar datos ----------------
    template_id = params.get("template_id") or "1SB9wFHWyDfd5P-24VBP7-dE1f1t7YvVYjnsc2XjqU8M"
    site_name = _dr_domain(site_url)
    today_str = _dr_iso(date.today())
    title = f"{site_name} - Discover Retention - {today_str}"

    sid = _dr_drive_copy_from_template(drive_service, template_id, title, dest_folder_id)
    sh = gs_client.open_by_key(sid)

    # Configuración
    ws_cfg = _dr_ws_ensure(sh, "Configuración")
    cfg_rows = [
        ["Configuración", "Valores"],
        ["Sitio Analizado", site_name],
        ["Tipo de análisis", "Discover Retention (compatibilidad diaria)"],
        ["Periodo analizado", f"{_dr_iso(start_dt)} a {_dr_iso(end_dt)}"],
    ]
    if path_filter:
        cfg_rows.append(["Sección", str(path_filter)])
    if country:
        cfg_rows.append(["País", str(country).upper()])
    _dr_write_ws(ws_cfg, cfg_rows)

    # Análisis
    ws_an = _dr_ws_ensure(sh, "Análisis")
    _dr_write_ws(ws_an, out)

    return sid

def _dr_build_minimal_sheet(gs_client, drive_service, site_url, start_dt, end_dt, path_filter, country, dest_folder_id):
    """Crea un Sheets desde el template pero con pestañas vacías (sin filas), manteniendo cabeceras estándares."""
    template_id = "1SB9wFHWyDfd5P-24VBP7-dE1f1t7YvVYjnsc2XjqU8M"
    site_name = _dr_domain(site_url)
    from datetime import date as _date
    title = f"{site_name} - Discover Retention - {_dr_iso(_date.today())}"
    sid = _dr_drive_copy_from_template(drive_service, template_id, title, dest_folder_id)
    sh = gs_client.open_by_key(sid)
    ws_cfg = _dr_ws_ensure(sh, "Configuración")
    cfg_rows = [
        ["Configuración", "Valores"],
        ["Sitio Analizado", site_name],
        ["Tipo de análisis", "Discover Retention (compatibilidad diaria)"],
        ["Periodo analizado", f"{_dr_iso(start_dt)} a {_dr_iso(end_dt)}"],
    ]
    if path_filter:
        cfg_rows.append(["Sección", str(path_filter)])
    if country:
        cfg_rows.append(["País", str(country).upper()])
    _dr_write_ws(ws_cfg, cfg_rows)

    ws_an = _dr_ws_ensure(sh, "Análisis")
    headers = [["URL","Clics del período","Impresiones del período","Sección","Fecha de publicación",
                "Hora de publicación","Fecha de ingreso a Discover","Hora de ingreso a Discover",
                "Días de permanencia","Última visualización en Discover","Status"]]
    _dr_write_ws(ws_an, headers)
    return sid

# ---- Wrapper público: intenta ext y, si falla por horas, usa compat diaria ----

_fn, _Params = _resolve_discover_retention()

def _wrap_run_discover_retention(ext_fn, ParamsCls):
    def _runner(sc_service, drive_service, gs_client, site_url: str, params: Any, dest_folder_id: Optional[str] = None):
        # Normalizar params para no romper __init__ de ParamsCls
        p_dict = params if isinstance(params, dict) else getattr(params, "__dict__", {}) or {}
        p_norm = _normalize_params_for_ext(p_dict)
        # Intentar con el runner externo
        try:
            if ParamsCls and not isinstance(params, ParamsCls):
                try:
                    params_ext = ParamsCls(**{k: v for k, v in p_norm.items() if k != "window"})
                except TypeError:
                    params_ext = p_norm
            else:
                params_ext = params
            return ext_fn(sc_service, drive_service, gs_client, site_url, params_ext, dest_folder_id)
        except Exception as e:
            if _dr_is_invalid_argument(e):
                # Fallback diario con aviso
                return _run_discover_retention_daily_compat(
                    sc_service, drive_service, gs_client, site_url, p_norm, dest_folder_id
                )
            # Otro error → re-lanzar
            raise
    return _runner

if _fn and _Params:
    run_discover_retention = _wrap_run_discover_retention(_fn, _Params)   # type: ignore[assignment]
    DiscoverRetentionParams = _Params                                     # type: ignore[assignment]
else:
    # Stub perezoso: reintenta resolver en el momento de la llamada
    def run_discover_retention(*args, **kwargs):  # type: ignore[no-redef]
        _fn2, _Params2 = _resolve_discover_retention()
        if _fn2 and _Params2:
            globals()["run_discover_retention"] = _wrap_run_discover_retention(_fn2, _Params2)
            globals()["DiscoverRetentionParams"] = _Params2
            return globals()["run_discover_retention"](*args, **kwargs)
        raise RuntimeError(
            "Falta seo_analisis_ext.run_discover_retention. "
            "Instalá/actualizá el paquete externo o agregá modules/discover_retention.py."
        )

    class DiscoverRetentionParams:  # type: ignore[no-redef]
        """Stub: se reemplaza automáticamente si el paquete externo/fallback está disponible."""
        pass

USING_EXT = bool(_ext)
EXT_PACKAGE = _ext

# =============================================================================
# Shim de normalización para run_content_analysis
# =============================================================================
def _rca_normalize_params(p: dict) -> dict:
    from datetime import date, timedelta
    if not isinstance(p, dict):
        return p

    # tipo
    raw_tipo = str(p.get("tipo", "")).strip().lower()
    if raw_tipo in ("ambos", "both", "search+discover", "search + discover", "search y discover"):
        tipo = "both"
    elif raw_tipo == "discover":
        tipo = "discover"
    elif raw_tipo == "search":
        tipo = "search"
    else:
        tipo = "both"
    p["tipo"] = tipo
    p.setdefault("source", tipo)
    p.setdefault("origen", "Search + Discover" if tipo == "both" else tipo.title())

    # ventana
    lag = int(p.get("lag_days", 3))
    win = dict(p.get("window") or {})
    per = dict(p.get("period") or {})

    start = (win.get("start") or win.get("start_date") or per.get("start") or per.get("start_date")
             or p.get("start") or p.get("start_date") or p.get("desde") or p.get("fecha_inicio"))
    end   = (win.get("end")   or win.get("end_date")   or per.get("end")   or per.get("end_date")
             or p.get("end")  or p.get("end_date")     or p.get("hasta")   or p.get("fecha_fin"))
    days  = per.get("days") or win.get("days") or p.get("days")

    def _iso(d):
        try:
            return d.isoformat()
        except Exception:
            return str(d)

    if not (start and end):
        if not days:
            days = 28
        end_dt = date.today() - timedelta(days=lag)
        start_dt = end_dt - timedelta(days=int(days) - 1)
        start = start or _iso(start_dt)
        end   = end   or _iso(end_dt)

    start = _iso(start); end = _iso(end)

    win.update({"start": start, "end": end, "start_date": start, "end_date": end})
    if "days" not in win and days is not None:
        win["days"] = days
    p["window"] = win

    per.update({"start": start, "end": end})
    if "days" not in per and days is not None:
        per["days"] = days
    p["period"] = per

    for k, v in [("start", start), ("end", end), ("desde", start), ("hasta", end),
                 ("fecha_inicio", start), ("fecha_fin", end)]:
        p[k] = v

    p["period_label"] = f"{start} a {end}"

    # filtros
    filters = dict(p.get("filters") or {})
    country = filters.get("country")
    if country in ("Todos", "", None):
        country = None
    filters["country"] = country
    filters.setdefault("pais", country)

    device = filters.get("device")
    if isinstance(device, str):
        dev = device.strip().lower()
        device = dev if dev in ("desktop", "mobile", "tablet") else None
    else:
        device = None
    filters["device"] = device
    filters.setdefault("dispositivo", device)

    sec_payload = filters.get("sections_payload") or filters.get("sections")
    if isinstance(sec_payload, dict) and sec_payload:
        filters["sections_payload"] = sec_payload
        filters.setdefault("sections", sec_payload)
    p["filters"] = filters

    # orden y límites
    order_by = str(p.get("order_by", "clicks")).strip().lower()
    if order_by not in ("clicks", "impressions", "ctr", "position"):
        order_by = "clicks"
    p["order_by"] = order_by

    try:
        p["max_urls"] = int(p.get("max_urls") or 300)
    except Exception:
        p["max_urls"] = 300

    return p

if run_content_analysis is not None:
    _ext_rca_fn = run_content_analysis

    def _rca_wrapper(sc_service, drive_service, gs_client, site_url, params, dest_folder_id=None, *args, **kwargs):
        import json as _json
        try:
            import streamlit as st
        except Exception:
            st = None
        norm_params = _rca_normalize_params(dict(params or {}))
        try:
            return _ext_rca_fn(sc_service, drive_service, gs_client, site_url, norm_params, dest_folder_id, *args, **kwargs)
        except Exception as e:
            if st is not None:
                st.session_state["_rca_norm_params"] = norm_params
                st.session_state["_rca_error"] = str(e)
                st.error(f"❌ Análisis de contenido falló: {e}")
                st.caption("Payload normalizado enviado al runner:")
                st.code(_json.dumps(norm_params, ensure_ascii=False, indent=2))
            return None

    run_content_analysis = _rca_wrapper

# =============================================================================
# Shim de normalización para run_content_structure
# =============================================================================
def _cs_normalize_params(p: dict) -> dict:
    if not isinstance(p, dict):
        return {}
    out = dict(p)

    def _iso(d):
        try:
            return d.isoformat()
        except Exception:
            return str(d)

    if "date_from" in out:
        out["date_from"] = _iso(out["date_from"])
    if "date_to" in out:
        out["date_to"] = _iso(out["date_to"])

    src = str(out.get("source", "both")).strip().lower()
    if src in ("search + discover", "search+discover", "both", "ambos"):
        src = "both"
    elif src in ("search", "web"):
        src = "search"
    elif src == "discover":
        src = "discover"
    else:
        src = "both"
    out["source"] = src

    order_by = str(out.get("order_by", "clicks")).strip().lower()
    if order_by not in ("clicks", "impressions", "ctr", "position"):
        order_by = "clicks"
    out["order_by"] = order_by

    def _to_int(key, default=None):
        try:
            out[key] = int(out.get(key)) if out.get(key) is not None else default
        except Exception:
            out[key] = default

    _to_int("row_limit", 500)
    _to_int("min_clicks", 0)
    _to_int("min_impressions", 0)
    _to_int("concurrency", 24)
    _to_int("timeout_s", 12)

    out["only_articles"] = bool(out.get("only_articles", True))
    out["entities"] = bool(out.get("entities", False))

    dev = out.get("device")
    if isinstance(dev, str):
        dev = dev.strip().upper()
        dev = dev if dev in ("DESKTOP", "MOBILE", "TABLET", "") else ""
    else:
        dev = ""
    out["device"] = dev or None

    cty = out.get("country")
    if isinstance(cty, str):
        cty = cty.strip().upper()
    out["country"] = cty or None

    out["joiner"] = out.get("joiner") or " | "
    out["ua"] = out.get("ua") or ""

    wants = dict(out.get("wants") or {})
    xpaths = dict(out.get("xpaths") or {})
    out["wants"] = wants
    out["xpaths"] = {k: (str(v) if v is not None else "") for k, v in xpaths.items()}

    out["sheet_title_prefix"] = out.get("sheet_title_prefix") or "Estructura contenidos"
    return out

if run_content_structure is not None:
    _ext_rcs_fn = run_content_structure

    def _rcs_wrapper(sc_service, drive_service, gs_client, site_url, params, dest_folder_id=None, *args, **kwargs):
        import json as _json
        try:
            import streamlit as st
        except Exception:
            st = None
        norm_params = _cs_normalize_params(dict(params or {}))
        try:
            return _ext_rcs_fn(sc_service, drive_service, gs_client, site_url, norm_params, dest_folder_id, *args, **kwargs)
        except Exception as e:
            if st is not None:
                st.session_state["_cs_norm_params"] = norm_params
                st.session_state["_cs_error"] = str(e)
                st.error(f"❌ Análisis de estructura de contenidos falló: {e}")
                st.caption("Payload normalizado enviado al runner:")
                st.code(_json.dumps(norm_params, ensure_ascii=False, indent=2))
            return None

    run_content_structure = _rcs_wrapper

# =============================================================================
# Parche de serialización segura al escribir a Sheets desde módulos externos
# =============================================================================
try:
    import pandas as pd  # type: ignore
except Exception:
    pd = None  # type: ignore
try:
    import numpy as np  # type: ignore
except Exception:
    np = None  # type: ignore
try:
    import datetime as _dt
except Exception:
    _dt = None  # type: ignore

def _patch_write_ws_if_present(module_name: str) -> None:
    """Si el módulo define _write_ws(...), lo parcheamos para serializar DataFrames de forma segura."""
    if pd is None or importlib is None:
        return
    try:
        mod = importlib.import_module(module_name)
    except Exception:
        return
    if not hasattr(mod, "_write_ws"):
        return

    _orig_write_ws = getattr(mod, "_write_ws")

    def _coerce_df_for_json(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        for c in out.columns:
            s = out[c]
            if hasattr(pd, "api") and pd.api.types.is_datetime64_any_dtype(s):
                out[c] = s.dt.strftime("%Y-%m-%d %H:%M:%S")
                continue

            def _cell_fix(x):
                try:
                    if x is None or (isinstance(x, float) and pd.isna(x)):
                        return None
                except Exception:
                    pass
                if getattr(pd, "NaT", object()) is x:
                    return None
                if _dt is not None and isinstance(x, (pd.Timestamp, _dt.datetime, _dt.date, _dt.time)):
                    try:
                        if isinstance(x, pd.Timestamp) and getattr(x, "tz", None) is not None:
                            x = x.tz_convert("UTC").tz_localize(None)
                    except Exception:
                        pass
                    return x.isoformat(sep=" ")
                if np is not None and isinstance(x, np.generic):
                    try:
                        return x.item()
                    except Exception:
                        return str(x)
                return x

            out[c] = s.map(_cell_fix)
        return out

    def _write_ws_patched(gs_client, spreadsheet, title, df_or_values):
        try:
            if pd is not None and isinstance(df_or_values, pd.DataFrame):
                safe_df = _coerce_df_for_json(df_or_values)
                return _orig_write_ws(gs_client, spreadsheet, title, safe_df)
            return _orig_write_ws(gs_client, spreadsheet, title, df_or_values)
        except TypeError:
            if pd is not None and isinstance(df_or_values, pd.DataFrame):
                return _orig_write_ws(gs_client, spreadsheet, title, df_or_values.astype(str))
            raise

    try:
        setattr(mod, "_write_ws", _write_ws_patched)
    except Exception:
        pass

for _candidate in [
    "seo_analisis_ext.discover_snoop",
    "seo_analisis_ext.content_analysis",
    "seo_analisis_ext.analysis_content",
    "seo_analisis_ext.content_structure",
    "seo_analisis_ext.discover_retention",  # añadido por si define _write_ws
    "seo_analisis_ext.utils_gsheets",
]:
    _patch_write_ws_if_present(_candidate)

__all__ = [
    "USING_EXT",
    "EXT_PACKAGE",
    "run_core_update",
    "run_evergreen",
    "run_traffic_audit",
    "run_names_analysis",
    "run_discover_snoop",
    "run_content_analysis",
    "run_content_structure",
    "run_sections_analysis",
    "run_report_results",
    "run_ga4_audience_report",
    "run_discover_retention",         # <-- NUEVO
    "DiscoverRetentionParams",        # <-- NUEVO
]
