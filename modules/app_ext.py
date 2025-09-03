# modules/app_ext.py
"""
Capa de compatibilidad para cargar analizadores desde el paquete externo
`seo_analisis_ext` (repo privado), con fallbacks locales.

Exporta:
- USING_EXT, EXT_PACKAGE
- run_core_update, run_evergreen, run_traffic_audit, run_names_analysis
- run_discover_snoop, run_content_analysis
- run_content_structure
- run_sections_analysis  <-- NUEVO
- run_report_results

Incluye:
- Shim robusto para run_content_analysis (normaliza fechas, tipo, filtros y alias)
- Shim de normalización para run_content_structure (fechas, source, filtros, scraping)
- Parche de serialización segura al escribir DataFrames a Google Sheets
"""

from modules.utils import ensure_external_package

_ext = ensure_external_package()

# =================== Preferimos funciones del paquete externo ===================

run_core_update        = getattr(_ext, "run_core_update", None) if _ext else None
run_evergreen          = getattr(_ext, "run_evergreen", None) if _ext else None
run_traffic_audit      = getattr(_ext, "run_traffic_audit", None) if _ext else None
run_names_analysis     = getattr(_ext, "run_names_analysis", None) if _ext else None
run_discover_snoop     = getattr(_ext, "run_discover_snoop", None) if _ext else None
run_content_analysis   = getattr(_ext, "run_content_analysis", None) if _ext else None
run_content_structure  = getattr(_ext, "run_content_structure", None) if _ext else None
run_sections_analysis  = getattr(_ext, "run_sections_analysis", None) if _ext else None  # <- NUEVO

# ============================= Fallbacks locales =================================

if (run_core_update is None) or (run_evergreen is None):
    try:
        from modules.analysis import run_core_update as _rcu, run_evergreen as _rev  # type: ignore
        run_core_update = run_core_update or _rcu
        run_evergreen   = run_evergreen   or _rev
    except Exception:
        pass

if run_traffic_audit is None:
    try:
        from modules.analysis import run_traffic_audit as _rta  # type: ignore
        run_traffic_audit = _rta
    except Exception:
        pass

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

# Discover Snoop
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

# Content Analysis
if run_content_analysis is None:
    _rca = None
    # Probar rutas alternativas dentro del paquete externo
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
    # Fallbacks locales (si existieran)
    if _rca is None:
        try:
            from modules.content_analysis import run_content_analysis as _rca  # type: ignore
        except Exception:
            try:
                from modules.analysis_content import run_content_analysis as _rca  # type: ignore
            except Exception:
                _rca = None
    run_content_analysis = _rca

# Content Structure
if run_content_structure is None:
    _rcs = None
    try:
        from seo_analisis_ext.content_structure import run_content_structure as _rcs  # type: ignore
    except Exception:
        try:
            from seo_analisis_ext.analysis_structure import run_content_structure as _rcs  # type: ignore
        except Exception:
            try:
                # Fallbacks locales opcionales, si los hubiera:
                # from modules.content_structure import run_content_structure as _rcs  # type: ignore
                _rcs = None
            except Exception:
                _rcs = None
    run_content_structure = _rcs

# Sections Analysis (nuevo)
if run_sections_analysis is None:
    _rsa = None
    try:
        from seo_analisis_ext.sections_analysis import run_sections_analysis as _rsa  # type: ignore
    except Exception:
        try:
            # Fallback local opcional si existiera:
            # from modules.sections_analysis import run_sections_analysis as _rsa  # type: ignore
            _rsa = None
        except Exception:
            _rsa = None
    run_sections_analysis = _rsa

USING_EXT = bool(_ext)
EXT_PACKAGE = _ext

# =============================================================================
# Shim de normalización para run_content_analysis
# =============================================================================
def _rca_normalize_params(p: dict) -> dict:
    from datetime import date, timedelta

    if not isinstance(p, dict):
        return p

    # ---------- tipo ----------
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

    # ---------- ventana ----------
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

    start = _iso(start)
    end = _iso(end)

    win["start"] = start
    win["end"] = end
    win["start_date"] = start
    win["end_date"] = end
    win.setdefault("days", days)
    p["window"] = win

    per["start"] = start
    per["end"] = end
    per.setdefault("days", days)
    p["period"] = per

    p["start"] = start
    p["end"] = end
    p["desde"] = start
    p["hasta"] = end
    p["fecha_inicio"] = start
    p["fecha_fin"] = end

    p["period_label"] = f"{start} a {end}"

    # ---------- filtros ----------
    filters = dict(p.get("filters") or {})
    country = filters.get("country")
    if country in ("Todos", "", None):
        country = None
    filters["country"] = country
    filters.setdefault("pais", country)

    device = filters.get("device")
    if isinstance(device, str):
        dev = device.strip().lower()
        if dev in ("desktop", "mobile", "tablet"):
            device = dev
        elif dev in ("todos", "", "none", None):
            device = None
    else:
        device = None
    filters["device"] = device
    filters.setdefault("dispositivo", device)

    sec_payload = filters.get("sections_payload") or filters.get("sections")
    if isinstance(sec_payload, dict) and sec_payload:
        filters["sections_payload"] = sec_payload
        filters.setdefault("sections", sec_payload)
    p["filters"] = filters

    # ---------- orden y límites ----------
    order_by = str(p.get("order_by", "clicks")).strip().lower()
    if order_by not in ("clicks", "impressions", "ctr", "position"):
        order_by = "clicks"
    p["order_by"] = order_by

    try:
        p["max_urls"] = int(p.get("max_urls") or 300)
    except Exception:
        p["max_urls"] = 300

    return p

# Envolver el runner de contenido con shim y manejo de errores visibles (sin relanzar)
if run_content_analysis is not None:
    _ext_rca_fn = run_content_analysis

    def _rca_wrapper(sc_service, drive_service, gs_client, site_url, params, dest_folder_id=None, *args, **kwargs):
        import json as _json
        try:
            import streamlit as st
        except Exception:
            st = None  # por si se ejecuta en entorno no-Streamlit

        norm_params = _rca_normalize_params(dict(params or {}))
        try:
            sid = _ext_rca_fn(sc_service, drive_service, gs_client, site_url, norm_params, dest_folder_id, *args, **kwargs)
            return sid
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

    # Fechas
    def _iso(d):
        try:
            return d.isoformat()
        except Exception:
            return str(d)

    if "date_from" in out:
        out["date_from"] = _iso(out["date_from"])
    if "date_to" in out:
        out["date_to"] = _iso(out["date_to"])

    # Source
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

    # Orden
    order_by = str(out.get("order_by", "clicks")).strip().lower()
    if order_by not in ("clicks", "impressions", "ctr", "position"):
        order_by = "clicks"
    out["order_by"] = order_by

    # Límites y números
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

    # Flags
    out["only_articles"] = bool(out.get("only_articles", True))
    out["entities"] = bool(out.get("entities", False))

    # Device/Country
    dev = out.get("device")
    if isinstance(dev, str):
        dev = dev.strip().upper()
        if dev not in ("DESKTOP", "MOBILE", "TABLET", ""):
            dev = ""
    else:
        dev = ""
    out["device"] = dev or None

    cty = out.get("country")
    if isinstance(cty, str):
        cty = cty.strip().upper()
    out["country"] = cty or None

    # Joiner y UA
    out["joiner"] = out.get("joiner") or " | "
    out["ua"] = out.get("ua") or ""

    # wants/xpaths dicts
    wants = dict(out.get("wants") or {})
    xpaths = dict(out.get("xpaths") or {})
    out["wants"] = wants
    out["xpaths"] = {k: (str(v) if v is not None else "") for k, v in xpaths.items()}

    # Prefijo título
    out["sheet_title_prefix"] = out.get("sheet_title_prefix") or "Estructura contenidos"

    return out

# Envolver el runner de estructura con shim y manejo de errores visibles (sin relanzar)
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
            sid = _ext_rcs_fn(sc_service, drive_service, gs_client, site_url, norm_params, dest_folder_id, *args, **kwargs)
            return sid
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
    import importlib
    import pandas as pd  # type: ignore
    import numpy as np   # type: ignore
    import datetime as _dt
except Exception:
    pd = None  # type: ignore
    np = None  # type: ignore

def _patch_write_ws_if_present(module_name: str) -> None:
    """Si el módulo define _write_ws(...), lo parcheamos para serializar DataFrames de forma segura."""
    if pd is None:
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
            if pd.api.types.is_datetime64_any_dtype(s):
                out[c] = s.dt.strftime("%Y-%m-%d %H:%M:%S")
                continue

            def _cell_fix(x):
                try:
                    if x is None or (isinstance(x, float) and pd.isna(x)):
                        return None
                except Exception:
                    pass
                if x is pd.NaT:
                    return None
                if isinstance(x, (pd.Timestamp, _dt.datetime, _dt.date, _dt.time)):
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
    "seo_analisis_ext.utils_gsheets",
]:
    _patch_write_ws_if_present(_candidate)

# --- Reporte de resultados (runner externo)
try:
    run_report_results = getattr(_ext, "run_report_results", None) if _ext else None
except Exception:
    run_report_results = None

# Fallback: traerla desde el submódulo si no está expuesta en __init__.py
if run_report_results is None:
    try:
        from seo_analisis_ext.report_results import run_report_results as _rrr  # type: ignore
        run_report_results = _rrr
    except Exception:
        run_report_results = None

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
    "run_sections_analysis",   # <- NUEVO
    "run_report_results",
]