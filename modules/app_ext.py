# modules/app_ext.py
"""
Capa de compatibilidad para cargar los analizadores desde el paquete externo
`seo_analisis_ext` cuando está disponible (repo privado), con fallbacks locales.

- Exporta: run_core_update, run_evergreen, run_traffic_audit, run_names_analysis,
          run_discover_snoop, run_content_analysis
- Define USING_EXT (bool) y EXT_PACKAGE (módulo externo o None)
- Aplica parches defensivos:
  (a) Normalización de parámetros para run_content_analysis (fechas y tipo)
  (b) Serialización segura al escribir DataFrames en Google Sheets
"""

from modules.utils import ensure_external_package

_ext = ensure_external_package()

# =================== Cargas desde paquete externo (si existe) ===================

run_core_update      = getattr(_ext, "run_core_update", None) if _ext else None
run_evergreen        = getattr(_ext, "run_evergreen", None) if _ext else None
run_traffic_audit    = getattr(_ext, "run_traffic_audit", None) if _ext else None
run_names_analysis   = getattr(_ext, "run_names_analysis", None) if _ext else None
run_discover_snoop   = getattr(_ext, "run_discover_snoop", None) if _ext else None
# NUEVO: Análisis de contenido
run_content_analysis = getattr(_ext, "run_content_analysis", None) if _ext else None

# ============================= Fallbacks locales ================================
# (para que la app no se rompa si el paquete externo no está instalado)

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

# Discover Snoop (si no vino del paquete externo)
if run_discover_snoop is None:
    _rds = None
    try:
        from seo_analisis_ext.discover_snoop import run_discover_snoop as _rds  # type: ignore
    except Exception:
        try:
            # Si tuvieras implementación local, podés activarla aquí:
            # from modules.discover_snoop import run_discover_snoop as _rds  # type: ignore
            # from modules.analysis_discover_snoop import run_discover_snoop as _rds  # type: ignore
            _rds = None
        except Exception:
            _rds = None
    run_discover_snoop = _rds

# Análisis de contenido (si no vino del paquete externo)
if run_content_analysis is None:
    _rca = None
    # Intentos en el paquete externo con distintos nombres posibles
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
    # Fallbacks locales opcionales
    if _rca is None:
        try:
            from modules.content_analysis import run_content_analysis as _rca  # type: ignore
        except Exception:
            try:
                from modules.analysis_content import run_content_analysis as _rca  # type: ignore
            except Exception:
                _rca = None
    run_content_analysis = _rca

USING_EXT = bool(_ext)
EXT_PACKAGE = _ext  # útil para localizar archivos (ej.: ai_summaries.py), si existe

# =============================================================================
# (a) SHIM DE NORMALIZACIÓN PARA run_content_analysis
# - Asegura window.start/window.end (ISO) y alias en period.*
# - Normaliza tipo a: search | discover | both
# - Rellena fechas a partir de window.days + lag_days si faltaran
# =============================================================================
def _rca_normalize_params(p: dict) -> dict:
    from datetime import date, timedelta

    if not isinstance(p, dict):
        return p

    # --- Normalizar tipo ---
    raw_tipo = (p.get("tipo") or "").strip().lower()
    if raw_tipo in ("ambos", "search + discover", "search y discover", "search+discover", "both"):
        tipo = "both"
    elif raw_tipo in ("search",):
        tipo = "search"
    elif raw_tipo in ("discover",):
        tipo = "discover"
    else:
        # fallback sensato
        tipo = "both"
    p["tipo"] = tipo

    # --- Normalizar ventana de fechas ---
    lag = int(p.get("lag_days", 3))
    win = dict(p.get("window") or {})
    start = win.get("start") or win.get("start_date") or p.get("start_date")
    end   = win.get("end")   or win.get("end_date")   or p.get("end_date")
    days  = win.get("days")

    # Si start/end vienen como date/datetime, casteo a string ISO (YYYY-MM-DD)
    def _iso(d):
        try:
            return d.isoformat()
        except Exception:
            return d

    # Si faltan fechas, las calculo desde hoy-lag y days
    if not (start and end):
        if days:
            end_dt = date.today() - timedelta(days=lag)
            start_dt = end_dt - timedelta(days=int(days) - 1)
            start = start or _iso(start_dt)
            end   = end or _iso(end_dt)
    start = _iso(start) if start else None
    end   = _iso(end)   if end   else None

    # Escribo en window y creo alias en period
    win["start"] = start
    win["end"] = end
    win["start_date"] = start
    win["end_date"] = end
    p["window"] = win

    # Alias para runners que miran "period"
    per = dict(p.get("period") or {})
    per["start"] = start
    per["end"] = end
    per["days"] = per.get("days") or days
    p["period"] = per

    return p

# Si tenemos un runner real de contenido, lo envolvemos con el shim
if run_content_analysis is not None:
    _ext_rca_fn = run_content_analysis

    def _rca_wrapper(sc_service, drive_service, gs_client, site_url, params, dest_folder_id=None, *args, **kwargs):
        try:
            norm_params = _rca_normalize_params(dict(params or {}))
        except Exception:
            norm_params = params
        return _ext_rca_fn(sc_service, drive_service, gs_client, site_url, norm_params, dest_folder_id, *args, **kwargs)

    run_content_analysis = _rca_wrapper

# =============================================================================
# (b) Parche de compatibilidad: evitar "TypeError: Object of type Timestamp
# is not JSON serializable" cuando se escriben DataFrames a Sheets desde
# runners externos (Discover Snoop / Análisis de contenido / etc).
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
    """
    Si `module_name` existe y define `_write_ws(gs_client, spreadsheet, title, df_or_values)`,
    lo parcheamos para garantizar serialización segura.
    """
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
            # 1) Columnas datetime -> string legible
            if pd.api.types.is_datetime64_any_dtype(s):
                out[c] = s.dt.strftime("%Y-%m-%d %H:%M:%S")
                continue

            # 2) En columnas object, convertir casos sueltos problemáticos
            def _cell_fix(x):
                # NaN / NaT
                try:
                    if x is None or (isinstance(x, float) and pd.isna(x)):
                        return None
                except Exception:
                    pass
                if x is pd.NaT:
                    return None
                # pandas/py datetime-like
                if isinstance(x, (pd.Timestamp, _dt.datetime, _dt.date, _dt.time)):
                    try:
                        if isinstance(x, pd.Timestamp) and x.tz is not None:
                            x = x.tz_convert("UTC").tz_localize(None)
                    except Exception:
                        pass
                    return x.isoformat(sep=" ")
                # numpy escalares -> nativos Python
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
            # Fallback ultra-defensivo: casteo completo a str
            if pd is not None and isinstance(df_or_values, pd.DataFrame):
                return _orig_write_ws(gs_client, spreadsheet, title, df_or_values.astype(str))
            raise

    try:
        setattr(mod, "_write_ws", _write_ws_patched)
    except Exception:
        # Nunca romper la app por el parche
        pass

# Intentamos parchear los módulos donde suele vivir _write_ws
for _candidate in [
    "seo_analisis_ext.discover_snoop",
    "seo_analisis_ext.content_analysis",
    "seo_analisis_ext.analysis_content",
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
]