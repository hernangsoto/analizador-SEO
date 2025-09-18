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
            """
            dimensions: p.ej. ["date"] o ["page"]
            filters: lista de dicts Search Analytics API (dimension/operator/expression)
            order_by: [{"field":"clicks","descending":True}] (opcional)
            """
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
            # Map de columnas visibles
            keep = []
            if metrics.get("clicks"):      keep.append("Clics")
            if metrics.get("impressions"): keep.append("Impresiones")
            if metrics.get("ctr"):         keep.append("CTR")
            if metrics.get("position"):    keep.append("Posición")
            # Asegurar alias
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
            """
            Crea un Sheet con:
              - Serie diaria (por origen)
              - Top de páginas global (y por país si se pidió), ordenado por Clics
            Soporta origen = "search" | "discover" | "both"
            """
            start = _rr__as_date(params.get("start"))
            end   = _rr__as_date(params.get("end"))
            origin = (params.get("origin") or "search").strip().lower()   # "search" | "discover" | "both"
            origin_list = ["search", "discover"] if origin == "both" else [origin]
            path = params.get("path") or None  # p.ej. "/vida/"
            countries = list(params.get("countries") or [])  # ISO3
            metrics = dict(params.get("metrics") or {"clicks": True, "impressions": True, "ctr": True, "position": origin != "discover"})
            top_n = int(params.get("top_n", 20))
            title_prefix = params.get("sheet_title_prefix") or "Reporte de resultados"

            # 1) Crear el Sheet
            site_pretty = _rr__domain_from_site(site_url)
            sheet_name = f"{title_prefix} ({start} a {end}) - {site_pretty}"
            meta = {"name": sheet_name, "mimeType": "application/vnd.google-apps.spreadsheet"}
            if dest_folder_id:
                meta["parents"] = [dest_folder_id]
            newfile = drive_service.files().create(body=meta, fields="id,name,webViewLink").execute()
            sid = newfile["id"]

            # 2) Abrir con gspread y preparar pestañas
            sh = gs_client.open_by_key(sid)
            ws0 = sh.sheet1
            ws0.update_title("Resumen")

            def _ensure(title: str):
                try:
                    return sh.worksheet(title)
                except Exception:
                    return sh.add_worksheet(title=title, rows=100, cols=20)

            # 3) Por cada origen → serie + tops
            for src in origin_list:
                label = "Search" if src == "search" else "Discover"

                # --- Serie diaria (global)
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

                # --- Top páginas Global
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

                # --- Top por país (si se pidió)
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

            # 4) Meta
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
# Discover Retention con normalización de params y resolución perezosa
# =============================================================================
def _dr_normalize_params(p: dict) -> dict:
    """Ajusta el payload del UI al constructor DiscoverRetentionParams del paquete externo.
       - Aplana window.start/end -> start/end (YYYY-MM-DD)
       - Settea defaults si faltan fechas (últimos 10 días con lag)
       - Fuerza tipos correctos (ints)
       - Elimina claves no soportadas
    """
    from datetime import date, datetime, timedelta

    q = dict(p or {})
    win = dict(q.pop("window", {}) or {})

    start = q.get("start") or win.get("start") or win.get("start_date")
    end   = q.get("end")   or win.get("end")   or win.get("end_date")
    lag   = int(q.get("lag_days", 2) or 0)
    lookback_days = int(q.get("days", 10) or 10)

    def _as_iso10(x):
        if x in (None, "", "None"):
            return None
        if isinstance(x, (date, datetime)):
            return x.isoformat()[:10]
        s = str(x).strip()
        # Acepta "YYYY-MM-DD" o "YYYY-MM-DDTHH:MM:SS"
        return s[:10] if len(s) >= 10 else s

    end_iso = _as_iso10(end)
    if not end_iso:
        end_iso = (date.today() - timedelta(days=lag)).isoformat()

    # si no viene start, toma ventana de 'lookback_days'
    start_iso = _as_iso10(start)
    if not start_iso:
        start_iso = (date.fromisoformat(end_iso) - timedelta(days=lookback_days - 1)).isoformat()

    # Si por error vienen invertidas, las ordenamos
    if start_iso > end_iso:
        start_iso, end_iso = end_iso, start_iso

    clean = {
        "start": start_iso,                     # YYYY-MM-DD
        "end": end_iso,                         # YYYY-MM-DD
        "lag_days": lag,                        # int
        "min_clicks": int(q.get("min_clicks", 0) or 0),
        "min_impressions": int(q.get("min_impressions", 0) or 0),
        "sheet_title_prefix": q.get("sheet_title_prefix")
                              or q.get("sheet_title_pref")
                              or "Incorp. y permanencia Discover",
        # No incluimos claves desconocidas para el dataclass
    }
    return clean

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

# --- reemplaza toda la función _wrap_dr por esta (y su uso permanece igual) ---
def _wrap_dr(fn):
    """Envoltura que limpia el payload y muestra debug en Streamlit si falla."""
    def _inner(sc_service, drive_service, gs_client, site_url, params, dest_folder_id=None, *args, **kwargs):
        import json as _json
        try:
            import streamlit as st
        except Exception:
            st = None

        try:
            params_clean = _dr_normalize_params(params)
        except Exception:
            params_clean = dict(params or {})
            params_clean.pop("window", None)

        try:
            return fn(sc_service, drive_service, gs_client, site_url, params_clean, dest_folder_id, *args, **kwargs)
        except Exception as e:
            # Modo debug visible sin tocar app.py
            if st is not None:
                st.session_state["_dr_norm_params"] = params_clean
                st.session_state["_dr_error"] = str(e)
                st.error(f"❌ Discover Retention falló: {e}")
                st.caption("Payload normalizado enviado al runner:")
                st.code(_json.dumps(params_clean, ensure_ascii=False, indent=2))
            # No re-lanzamos: devolvemos None para que el UI siga vivo
            return None
    return _inner
# Intento de resolución al importar el módulo
_fn, _Params = _resolve_discover_retention()
if _fn and _Params:
    run_discover_retention = _wrap_dr(_fn)     # type: ignore[assignment]
    DiscoverRetentionParams = _Params          # type: ignore[assignment]
else:
    # Stub perezoso: reintenta resolver y envuelve en el momento de la llamada
    def run_discover_retention(*args, **kwargs):  # type: ignore[no-redef]
        _fn2, _Params2 = _resolve_discover_retention()
        if _fn2 and _Params2:
            globals()["run_discover_retention"] = _wrap_dr(_fn2)
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
