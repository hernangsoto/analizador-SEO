# modules/app_ext.py
# Carga runners desde el paquete externo si existe, con fallbacks locales
from modules.utils import ensure_external_package

_ext = ensure_external_package()

run_core_update     = getattr(_ext, "run_core_update", None) if _ext else None
run_evergreen       = getattr(_ext, "run_evergreen", None) if _ext else None
run_traffic_audit   = getattr(_ext, "run_traffic_audit", None) if _ext else None
run_names_analysis  = getattr(_ext, "run_names_analysis", None) if _ext else None
# ⬇️ NUEVO: Discover Snoop
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

# ⬇️ Fallbacks para Discover Snoop
if run_discover_snoop is None:
    _rds = None
    try:
        # Si también distribuyes este runner en el paquete secundario
        from seo_analisis_ext.discover_snoop import run_discover_snoop as _rds  # type: ignore
    except Exception:
        try:
            # Implementación local (elige el nombre de módulo que uses)
            from modules.discover_snoop import run_discover_snoop as _rds  # type: ignore
        except Exception:
            try:
                # Alternativa local
                from modules.analysis_discover_snoop import run_discover_snoop as _rds  # type: ignore
            except Exception:
                _rds = None
    run_discover_snoop = _rds

USING_EXT = bool(_ext)
EXT_PACKAGE = _ext  # útil para localizar archivos (ai_summaries.py), si existe

__all__ = [
    "USING_EXT",
    "EXT_PACKAGE",
    "run_core_update",
    "run_evergreen",
    "run_traffic_audit",
    "run_names_analysis",
    "run_discover_snoop",
]