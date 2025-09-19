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
    * Permite forzar modo Diario (compat) con params.force_daily_compat/force_compat_daily.
    * En modo Diario muestra un aviso en la UI (Streamlit) y arma el Sheets con el template.
    * Incluye Helper de Depuración de publicación (pestaña "Debug Publicación").
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

run_core_update = _get_ext_attr("run_core_update")
run_evergreen = _get_ext_attr("run_evergreen")
run_traffic_audit = _get_ext_attr("run_traffic_audit")
run_names_analysis = _get_ext_attr("run_names_analysis")
run_discover_snoop = _get_ext_attr("run_discover_snoop")
run_content_analysis = _get_ext_attr("run_content_analysis")
run_content_structure = _get_ext_attr("run_content_structure")
run_sections_analysis = _get_ext_attr("run_sections_analysis")
run_report_results = _get_ext_attr("run_report_results")
run_ga4_audience_report = _get_ext_attr("run_ga4_audience_report")
# Nuevo análisis Discover Retention (preferir export desde __init__, si existe)
run_discover_retention = _get_ext_attr("run_discover_retention")
DiscoverRetentionParams = _get_ext_attr("DiscoverRetentionParams")

# ============================= Fallbacks ========================================

# Core / Evergreen (local)
if (run_core_update is None) or (run_evergreen is None):
    try:
        from modules.analysis import run_core_update as _rcu, run_evergreen as _rev  # type: ignore

        run_core_update = run_core_update or _rcu
        run_evergreen = run_evergreen or _rev
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

        def _rr__gsc_query(
            sc,
            site,
            start,
            end,
            search_type,
            dimensions,
            filters=None,
            row_limit=25000,
            order_by=None,
        ):
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
            if metrics.get("clicks"):
                keep.append("Clics")
            if metrics.get("impressions"):
                keep.append("Impresiones")
            if metrics.get("ctr"):
                keep.append("CTR")
            if metrics.get("position"):
                keep.append("Posición")
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

        def run_report_results(
            sc_service,
            drive_service,
            gs_client,
            site_url: str,
            params: dict,
            dest_folder_id: str | None = None,
        ) -> str | None:  # type: ignore[override]
            start = _rr__as_date(params.get("start"))
            end = _rr__as_date(params.get("end"))
            origin = (params.get("origin") or "search").strip().lower()
            origin_list = ["search", "discover"] if origin == "both" else [origin]
            path = params.get("path") or None
            countries = list(params.get("countries") or [])
            metrics = dict(
                params.get(
                    "metrics",
                    {"clicks": True, "impressions": True, "ctr": True, "position": origin != "discover"},
                )
            )
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
                        sc_service, site_url, start, end, src, dimensions=["date"], filters=filters, row_limit=25000
                    )
                    if not df_series.empty:
                        df_series = df_series.groupby(["date"], as_index=False).agg(
                            {"clicks": "sum", "impressions": "sum", "ctr": "mean", "position": "mean"}
                        )
                        df_series = _rr__apply_metrics(df_series, metrics)
                except Exception:
                    import pandas as _pd

                    df_series = _pd.DataFrame()
                _rr__write_ws(_ensure(f"Serie diaria ({label})"), df_series)

                try:
                    df_top_global = _rr__gsc_query(
                        sc_service,
                        site_url,
                        start,
                        end,
                        src,
                        dimensions=["page"],
                        filters=filters,
                        row_limit=max(1000, top_n if top_n > 0 else 1000),
                        order_by=[{"field": "clicks", "descending": True}],
                    )
                    if not df_top_global.empty:
                        df_top_global = (
                            df_top_global.sort_values("clicks", ascending=False).groupby("page", as_index=False).first()
                        )
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
                            sc_service,
                            site_url,
                            start,
                            end,
                            src,
                            dimensions=["page", "country"],
                            filters=filters_iso,
                            row_limit=max(1000, top_n if top_n > 0 else 1000),
                            order_by=[{"field": "clicks", "descending": True}],
                        )
                        if not df_top_ctry.empty:
                            df_top_ctry = (
                                df_top_ctry.sort_values("clicks", ascending=False)
                                .groupby("page", as_index=False)
                                .first()
                            )
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
                info = _pd.DataFrame(
                    {
                        "campo": [
                            "site_url",
                            "start",
                            "end",
                            "origin",
                            "path",
                            "countries",
                            "top_n",
                            "metrics",
                        ],
                        "valor": [
                            site_url,
                            str(start),
                            str(end),
                            origin,
                            path or "(todo el sitio)",
                            ", ".join([c.upper() for c in countries]) if countries else "(Global)",
                            top_n,
                            ", ".join([k for k, v in metrics.items() if v]) or "(ninguna)",
                        ],
                    }
                )
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

from typing import Any, Optional, Tuple, Dict, List


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
    start = (
        p.get("start")
        or win.get("start")
        or per.get("start")
        or p.get("start_date")
        or p.get("fecha_inicio")
    )
    end = p.get("end") or win.get("end") or per.get("end") or p.get("end_date") or p.get("fecha_fin")
    if start:
        p["start"] = _dr_iso(start)
    if end:
        p["end"] = _dr_iso(end)
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


# ---- Fallback Diario (con aviso en UI) + DEPURACIÓN --------------------------

def _run_discover_retention_daily_compat(
    sc_service,
    drive_service,
    gs_client,
    site_url: str,
    params: dict,
    dest_folder_id: Optional[str] = None,
) -> Optional[str]:
    """
    Modo compatibilidad (sin HOUR/HOURLY_ALL):
      - Arma Sheets desde template
      - Completa Configuración
      - Serie diaria Discover por URL (GSC)
      - Scraping concurrente para Fecha/Hora de publicación:
          * <meta ... property|name|itemprop = (article:published_time | datePublished | parsely-pub-date | ... ) />
          * JSON-LD (datePublished)
          * <time datetime="...">
      - Si params['debug_pubdate'] = True, genera la pestaña "Debug Publicación" con trazas.
    """
    import pandas as pd  # type: ignore
    from datetime import date, datetime, timedelta
    import re
    import json
    import html as _html
    import gzip
    from concurrent.futures import ThreadPoolExecutor, as_completed

    DEBUG_PUBDATE = bool(params.get("debug_pubdate") or params.get("debug_publication"))

    # Contenedor de logs de depuración (se escriben al final)
    pub_debug_logs: List[List[str]] = []

    def _dbg(url: str, source: str, key: str, raw_value: str, dt: Optional[datetime], step: str):
        if not DEBUG_PUBDATE:
            return
        try:
            val = raw_value if raw_value is not None else ""
            if len(val) > 300:
                val = val[:300] + "…"
        except Exception:
            val = ""
        pub_debug_logs.append(
            [
                url or "",
                source or "",
                key or "",
                val,
                "1" if dt else "0",
                (dt.date().isoformat() if dt else ""),
                (dt.strftime("%H:%M") if dt else ""),
                step or "",
            ]
        )

    # ---------------- Fechas del período ----------------
    st = _dr_try_import_streamlit()
    start = params.get("start") or params.get("start_date")
    end = params.get("end") or params.get("end_date")
    if not start or not end:
        days = int(params.get("days") or params.get("period", {}).get("days") or 10)
        end_dt = date.today() - timedelta(days=1)
        start_dt = end_dt - timedelta(days=days - 1)
    else:
        start_dt = _dr_to_date(start)
        end_dt = _dr_to_date(end)
        if start_dt > end_dt:
            start_dt, end_dt = end_dt, start_dt

    # Aviso UI (caímos a diario)
    if st is not None:
        key = f"_dr_hourly_fallback::{_dr_domain(site_url)}::{_dr_iso(start_dt)}::{_dr_iso(end_dt)}"
        if not st.session_state.get(key):
            st.session_state[key] = True
            st.warning(
                "⚠️ La propiedad de GSC aún no devuelve datos por **hora** para Discover vía API. "
                "Se ejecuta en **modo Diario (compatibilidad)**."
            )
            st.caption("Las columnas de **hora** de ingreso a Discover quedarán vacías en este modo.")

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
        # Crear sheet mínimo (con cabeceras) y fin
        return _dr_build_minimal_sheet(
            gs_client, drive_service, site_url, start_dt, end_dt, path_filter, country, dest_folder_id
        )

    df = pd.DataFrame(
        [
            {"date": r["keys"][0], "url": r["keys"][1], "clicks": r.get("clicks", 0), "impressions": r.get("impressions", 0)}
            for r in rows
        ]
    )
    df["date"] = pd.to_datetime(df["date"]).dt.date

    grp = df.groupby("url", as_index=False).agg(
        clicks=("clicks", "sum"),
        impressions=("impressions", "sum"),
        first_date=("date", "min"),
        last_date=("date", "max"),
    )
    grp["section"] = grp["url"].map(_dr_extract_section)
    grp["dias_perm"] = (grp["last_date"] - grp["first_date"]).dt.days

    # =================== SCRAPING PUBLICACIÓN (MEJORADO) ===================

    # a) Config
    total = len(grp)
    top_n = min(total, int(params.get("max_pubdate_fetch", 2000)))  # por defecto hasta 2000
    concurrency = int(params.get("pubdate_concurrency", 10))
    timeout_s = float(params.get("pubdate_timeout", 8.0))
    ua = params.get("ua") or (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )

    urls_ranked = grp.sort_values("clicks", ascending=False)["url"].tolist()
    urls_fetch = urls_ranked[:top_n]

    # b) Helpers parsing HTML
    META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.I | re.S)
    ATTR_RE = re.compile(r'([^\s=/>]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')', re.I | re.S)
    SCRIPT_LD_RE = re.compile(
        r'<script\b[^>]*type\s*=\s*["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        re.I | re.S,
    )
    TIME_DT_RE = re.compile(r'<time\b[^>]*datetime\s*=\s*["\']([^"\']+)["\']', re.I | re.S)

    KEYS_PRIORIDAD = (
        "article:published_time",
        "og:article:published_time",
        "og:published_time",
        "parsely-pub-date",
        "sailthru.date",
        "pubdate",
        "publishdate",
        "datepublished",
        "dc.date",
        "dc.date.issued",
        "dc.date.published",
        "date",
        "prism.publishdate",
        "fb:publication_time",
    )
    ITEMPROP_CANDIDATES = ("datepublished", "datecreated", "dateissued", "datemodified")

    def _try_parse_dt(s: str):
        s = (s or "").strip()
        if not s:
            return None
        from datetime import datetime as _dt2

        try:
            from dateutil import parser as dp  # type: ignore

            return dp.parse(s)
        except Exception:
            pass
        s2 = s.replace("Z", "+00:00")
        try:
            return _dt2.fromisoformat(s2)
        except Exception:
            pass
        m = re.search(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}(?::\d{2})?)", s)
        if m:
            try:
                ss = m.group(1) + "T" + m.group(2)
                return _dt2.fromisoformat(ss)
            except Exception:
                return None
        m = re.search(r"(\d{4}-\d{2}-\d{2})", s)
        if m:
            try:
                return _dt2.fromisoformat(m.group(1))
            except Exception:
                return None
        return None

    def _extract_from_meta(url: str, html_text: str):
        for tag in META_TAG_RE.findall(html_text or ""):
            attrs = {m.group(1).lower(): (m.group(2) or m.group(3) or "") for m in ATTR_RE.finditer(tag)}
            key = (attrs.get("property") or attrs.get("name") or attrs.get("itemprop") or "").strip().lower()
            val = _html.unescape((attrs.get("content") or attrs.get("value") or "").strip())
            if not key or not val:
                continue
            if key in KEYS_PRIORIDAD:
                dt = _try_parse_dt(val)
                _dbg(url, "meta", key, val, dt, "meta-property/name")
                if dt:
                    return dt, "meta", key, val
            if key in ITEMPROP_CANDIDATES:
                dt = _try_parse_dt(val)
                _dbg(url, "meta", key, val, dt, "meta-itemprop")
                if dt:
                    return dt, "meta", key, val
        return None, "", "", ""

    def _walk_json(url, o):
        if isinstance(o, dict):
            if "@type" in o and str(o["@type"]).lower() in ("newsarticle", "article", "report", "blogposting"):
                for key in ("datePublished", "dateCreated", "dateModified"):
                    if key in o and isinstance(o[key], str):
                        dt = _try_parse_dt(o[key])
                        _dbg(url, "ld+json", key, o[key], dt, "ld-main")
                        if dt:
                            return dt, "ld+json", key, o[key]
            for k, v in o.items():
                if isinstance(v, str) and k.lower() in ("datepublished", "datecreated", "datemodified"):
                    dt = _try_parse_dt(v)
                    _dbg(url, "ld+json", k, v, dt, "ld-walk")
                    if dt:
                        return dt, "ld+json", k, v
                if isinstance(v, (dict, list)):
                    got = _walk_json(url, v)
                    if got and got[0]:
                        return got
        elif isinstance(o, list):
            for it in o:
                got = _walk_json(url, it)
                if got and got[0]:
                    return got
        return None, "", "", ""

    def _extract_from_ld(url: str, html_text: str):
        for block in SCRIPT_LD_RE.findall(html_text or ""):
            raw = _html.unescape(block)
            try:
                data = json.loads(raw)
                got = _walk_json(url, data)
                if got and got[0]:
                    return got
            except Exception:
                m = re.search(r'"datePublished"\s*:\s*"([^"]+)"', raw)
                val = m.group(1) if m else ""
                dt = _try_parse_dt(val) if val else None
                _dbg(url, "ld+json", "datePublished", val, dt, "ld-regex")
                if dt:
                    return dt, "ld+json", "datePublished", val
        return None, "", "", ""

    def _extract_from_time(url: str, html_text: str):
        m = TIME_DT_RE.search(html_text or "")
        val = m.group(1) if m else ""
        dt = _try_parse_dt(val) if val else None
        _dbg(url, "time", "datetime", val, dt, "time-datetime")
        if dt:
            return dt, "time", "datetime", val
        return None, "", "", ""

    # c) Fetch HTML
    def _fetch_html(url: str) -> str:
        try:
            import requests  # type: ignore

            headers = {
                "User-Agent": ua,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip, deflate",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            }
            r = requests.get(url, headers=headers, timeout=timeout_s, allow_redirects=True)
            r.raise_for_status()
            enc = r.encoding or "utf-8"
            return r.text if r.text else r.content.decode(enc, errors="ignore")
        except Exception as e:
            _dbg(url, "fetch", "requests", repr(e), None, "fetch-error")
        try:
            from urllib.request import Request, urlopen

            req = Request(url, headers={"User-Agent": ua, "Accept-Encoding": "gzip, deflate"})
            with urlopen(req, timeout=timeout_s) as resp2:
                data = resp2.read()
                enc_hdr = (resp2.headers.get("Content-Encoding") or "").lower()
                if "gzip" in enc_hdr:
                    try:
                        data = gzip.decompress(data)
                    except Exception:
                        pass
                try:
                    return data.decode("utf-8")
                except Exception:
                    try:
                        return data.decode("latin-1", errors="ignore")
                    except Exception:
                        return ""
        except Exception as e:
            _dbg(url, "fetch", "urllib", repr(e), None, "fetch-error2")
            return ""

    def _fetch_and_extract(url: str) -> tuple[str, str, str]:
        try:
            html_text = _fetch_html(url)
            if DEBUG_PUBDATE:
                _dbg(url, "fetch", "length", str(len(html_text)), None, "fetched")
            dt, src, key, val = _extract_from_meta(url, html_text)
            if not dt:
                dt, src, key, val = _extract_from_ld(url, html_text)
            if not dt:
                dt, src, key, val = _extract_from_time(url, html_text)
            if dt and DEBUG_PUBDATE:
                _dbg(url, src or "unknown", key or "", val or "", dt, "FINAL")
            if dt:
                return url, dt.date().isoformat(), dt.strftime("%H:%M")
            return url, "", ""
        except Exception as e:
            _dbg(url, "extract", "exception", repr(e), None, "extract-error")
            return url, "", ""

    if urls_fetch:
        if st is not None:
            st.caption(f"⏳ Extrayendo fecha/hora de publicación en {len(urls_fetch)} URLs (máx={top_n})…")
        pub_date_map: dict[str, tuple[str, str]] = {}
        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            futures = [ex.submit(_fetch_and_extract, u) for u in urls_fetch]
            for f in as_completed(futures):
                u, d, h = f.result()
                pub_date_map[u] = (d, h)
        if st is not None:
            st.caption(
                f"✅ Publicación detectada en {sum(1 for v in pub_date_map.values() if v[0])}/{len(urls_fetch)} URLs."
            )
    else:
        pub_date_map = {}

    grp["fecha_pub"] = grp["url"].map(lambda u: pub_date_map.get(u, ("", ""))[0])
    grp["hora_pub"] = grp["url"].map(lambda u: pub_date_map.get(u, ("", ""))[1])

    # =================== Resto de columnas ===================
    grp["hora_ingreso"] = ""  # no hay horas en modo compat
    grp["ultima_vis"] = grp["last_date"].astype(str)

    s_start, s_end = start_dt, end_dt

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

    grp["status"] = [
        _status_row(fd, ld, fp) for fd, ld, fp in zip(grp["first_date"], grp["last_date"], grp["fecha_pub"])
    ]

    out = grp[
        [
            "url",
            "clicks",
            "impressions",
            "section",
            "fecha_pub",
            "hora_pub",
            "first_date",
            "hora_ingreso",
            "dias_perm",
            "ultima_vis",
            "status",
        ]
    ].rename(
        columns={
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
        }
    )

    # =================== Sheets desde template ===================
    template_id = params.get("template_id") or "1SB9wFHWyDfd5P-24VBP7-dE1f1t7YvVYjnsc2XjqU8M"
    site_name = _dr_domain(site_url)
    today_str = _dr_iso(date.today())
    title = f"{site_name} - Discover Retention - {today_str}"

    sid = _dr_drive_copy_from_template(drive_service, template_id, title, dest_folder_id)
    sh = gs_client.open_by_key(sid)

    ws_cfg = _dr_ws_ensure(sh, "Configuración")
    cfg_rows = [
        ["Configuración", "Valores"],
        ["Sitio Analizado", site_name],
        ["Tipo de análisis", "Discover Retention (compatibilidad diaria)"],
        ["Periodo analizado", f"{_dr_iso(start_dt)} a {_dr_iso(end_dt)}"],
        ["URLs analizadas (pubdate)", str(len(urls_fetch))],
        ["Debug pubdate activo", "Sí" if DEBUG_PUBDATE else "No"],
    ]
    if path_filter:
        cfg_rows.append(["Sección", str(path_filter)])
    if country:
        cfg_rows.append(["País", str(country).upper()])
    _dr_write_ws(ws_cfg, cfg_rows)

    ws_an = _dr_ws_ensure(sh, "Análisis")
    _dr_write_ws(ws_an, out)

    # =================== Debug: escribir logs ===================
    if DEBUG_PUBDATE:
        ws_dbg = _dr_ws_ensure(sh, "Debug Publicación")
        if pub_debug_logs:
            import pandas as pd

            df_dbg = pd.DataFrame(
                pub_debug_logs, columns=["URL", "Fuente", "Clave", "Valor", "Parse_OK", "Fecha", "Hora", "Paso"]
            )
            _dr_write_ws(ws_dbg, df_dbg)
        else:
            _dr_write_ws(ws_dbg, [["(sin eventos de depuración)"]])

    return sid


def _dr_build_minimal_sheet(
    gs_client, drive_service, site_url, start_dt, end_dt, path_filter, country, dest_folder_id
):
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
    headers = [
        [
            "URL",
            "Clics del período",
            "Impresiones del período",
            "Sección",
            "Fecha de publicación",
            "Hora de publicación",
            "Fecha de ingreso a Discover",
            "Hora de ingreso a Discover",
            "Días de permanencia",
            "Última visualización en Discover",
            "Status",
        ]
    ]
    _dr_write_ws(ws_an, headers)
    return sid


# ---- Wrapper público: intenta ext y, si falla por horas, usa compat diaria ----

_fn, _Params = _resolve_discover_retention()


def _wrap_run_discover_retention(ext_fn, ParamsCls):
    def _runner(sc_service, drive_service, gs_client, site_url: str, params: Any, dest_folder_id: Optional[str] = None):
        p_dict = params if isinstance(params, dict) else getattr(params, "__dict__", {}) or {}
        p_norm = _normalize_params_for_ext(p_dict)

        # Forzar compat diario si se solicita (útil para depurar)
        if p_norm.get("force_daily_compat") or p_norm.get("force_compat_daily"):
            return _run_discover_retention_daily_compat(
                sc_service, drive_service, gs_client, site_url, p_norm, dest_folder_id
            )

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
                return _run_discover_retention_daily_compat(
                    sc_service, drive_service, gs_client, site_url, p_norm, dest_folder_id
                )
            raise

    return _runner


if _fn and _Params:
    run_discover_retention = _wrap_run_discover_retention(_fn, _Params)  # type: ignore[assignment]
    DiscoverRetentionParams = _Params  # type: ignore[assignment]
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

    start = (
        win.get("start")
        or win.get("start_date")
        or per.get("start")
        or per.get("start_date")
        or p.get("start")
        or p.get("start_date")
        or p.get("desde")
        or p.get("fecha_inicio")
    )
    end = (
        win.get("end")
        or win.get("end_date")
        or per.get("end")
        or per.get("end_date")
        or p.get("end")
        or p.get("end_date")
        or p.get("hasta")
        or p.get("fecha_fin")
    )
    days = per.get("days") or win.get("days") or p.get("days")

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
        end = end or _iso(end_dt)

    start = _iso(start)
    end = _iso(end)

    win.update({"start": start, "end": end, "start_date": start, "end_date": end})
    if "days" not in win and days is not None:
        win["days"] = days
    p["window"] = win

    per.update({"start": start, "end": end})
    if "days" not in per and days is not None:
        per["days"] = days
    p["period"] = per

    for k, v in [
        ("start", start),
        ("end", end),
        ("desde", start),
        ("hasta", end),
        ("fecha_inicio", start),
        ("fecha_fin", end),
    ]:
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
            return _ext_rca_fn(
                sc_service, drive_service, gs_client, site_url, norm_params, dest_folder_id, *args, **kwargs
            )
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
            return _ext_rcs_fn(
                sc_service, drive_service, gs_client, site_url, norm_params, dest_folder_id, *args, **kwargs
            )
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
    "seo_analisis_ext.discover_retention",
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
    "run_discover_retention",  # <-- NUEVO
    "DiscoverRetentionParams",  # <-- NUEVO
]
