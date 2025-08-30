# modules/app_ext.py
# Carga runners desde el paquete externo si existe, con fallbacks locales
from modules.utils import ensure_external_package

_ext = ensure_external_package()

run_core_update     = getattr(_ext, "run_core_update", None) if _ext else None
run_evergreen       = getattr(_ext, "run_evergreen", None) if _ext else None
run_traffic_audit   = getattr(_ext, "run_traffic_audit", None) if _ext else None
run_names_analysis  = getattr(_ext, "run_names_analysis", None) if _ext else None
# NUEVO: Discover Snoop
run_discover_snoop  = getattr(_ext, "run_discover_snoop", None) if _ext else None

# ----------------- Fallbacks locales -----------------

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

# Fallbacks para Discover Snoop (si no viene del paquete externo)
if run_discover_snoop is None:
    _rds = None
    try:
        from seo_analisis_ext.discover_snoop import run_discover_snoop as _rds  # type: ignore
    except Exception:
        try:
            # Si tuvieras una implementación local, descomenta una de estas:
            # from modules.discover_snoop import run_discover_snoop as _rds  # type: ignore
            # from modules.analysis_discover_snoop import run_discover_snoop as _rds  # type: ignore
            _rds = None
        except Exception:
            _rds = None
    run_discover_snoop = _rds

USING_EXT = bool(_ext)
EXT_PACKAGE = _ext  # útil para localizar archivos (ai_summaries.py), si existe

# =====================================================================
# Parche de compatibilidad: evitar "TypeError: Object of type Timestamp
# is not JSON serializable" cuando discover_snoop escribe a Sheets.
#
# Convertimos Timestamps/fechas/np types a tipos JSON-serializables
# (strings o nativos de Python) *antes* de que gspread haga el update.
# =====================================================================
try:
    import importlib
    import pandas as pd  # type: ignore
    import numpy as np    # type: ignore
    import datetime as _dt

    _mod_ds = None
    # Solo intentamos si el módulo externo existe
    try:
        _mod_ds = importlib.import_module("seo_analisis_ext.discover_snoop")
    except Exception:
        _mod_ds = None

    if _mod_ds is not None and hasattr(_mod_ds, "_write_ws"):
        _orig_write_ws = _mod_ds._write_ws  # guardamos el original

        def _coerce_df_for_json(df: pd.DataFrame) -> pd.DataFrame:
            """Convierte columnas problemáticas a strings/valores nativos."""
            out = df.copy()
            for c in out.columns:
                s = out[c]
                # 1) Columnas datetime -> string legible
                if pd.api.types.is_datetime64_any_dtype(s):
                    out[c] = s.dt.strftime("%Y-%m-%d %H:%M:%S")
                    continue

                # 2) En columnas object, convertir Timestamps/datetimes sueltos
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
                            # normalizamos tz si la hubiera
                            if isinstance(x, pd.Timestamp) and x.tz is not None:
                                x = x.tz_convert("UTC").tz_localize(None)
                        except Exception:
                            pass
                        # iso con espacio (más legible en Sheets)
                        return x.isoformat(sep=" ")
                    # numpy escalares -> nativos Python
                    if isinstance(x, np.generic):
                        return x.item()
                    return x

                out[c] = s.map(_cell_fix)
            return out

        def _write_ws_patched(gs_client, spreadsheet, title, df_or_values):
            """Wrapper que asegura serialización segura antes de llamar al original."""
            try:
                if isinstance(df_or_values, pd.DataFrame):
                    safe_df = _coerce_df_for_json(df_or_values)
                    return _orig_write_ws(gs_client, spreadsheet, title, safe_df)
                # Si no es DataFrame, delegamos tal cual
                return _orig_write_ws(gs_client, spreadsheet, title, df_or_values)
            except TypeError:
                # Fallback ultra-defensivo: casteo completo a str
                if isinstance(df_or_values, pd.DataFrame):
                    return _orig_write_ws(gs_client, spreadsheet, title, df_or_values.astype(str))
                raise

        # Aplicamos el parche
        _mod_ds._write_ws = _write_ws_patched

except Exception:
    # Nunca romper la app por el parche
    pass

__all__ = [
    "USING_EXT",
    "EXT_PACKAGE",
    "run_core_update",
    "run_evergreen",
    "run_traffic_audit",
    "run_names_analysis",
    "run_discover_snoop",
]