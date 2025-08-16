# modules/analysis.py
from __future__ import annotations

from typing import Optional, Tuple

# Mostramos errores de forma amigable en la UI si faltan funciones
import streamlit as st

# Intentamos usar el paquete externo si está instalado
_HAVE_EXT = False
try:
    from seo_analisis_ext.core_update import run_core_update as _run_core_update  # type: ignore
    from seo_analisis_ext.evergreen import run_evergreen as _run_evergreen        # type: ignore
    _HAVE_EXT = True
except Exception:
    _HAVE_EXT = False


def _missing(feature: str) -> None:
    st.error(
        f"Este despliegue no incluye **{feature}** porque el paquete externo "
        f"no está disponible. "
        f"Configurá los secrets de `ext_repo`/`ext_token`/`ext_package` "
        f"o embebe una implementación local."
    )
    st.stop()  # detenemos elegantemente la ejecución de la acción actual


def run_core_update(
    sc_service,
    drive,
    gsclient,
    site_url: str,
    params: Tuple,
    dest_folder_id: Optional[str] = None,
) -> str:
    """
    Delegado: usa paquete externo si existe; si no, muestra mensaje y detiene.
    Debe devolver el ID del spreadsheet (str) cuando se completa.
    """
    if _HAVE_EXT:
        return _run_core_update(sc_service, drive, gsclient, site_url, params, dest_folder_id)
    _missing("run_core_update")


def run_evergreen(
    sc_service,
    drive,
    gsclient,
    site_url: str,
    params: Tuple,
    dest_folder_id: Optional[str] = None,
) -> str:
    """
    Delegado: usa paquete externo si existe; si no, muestra mensaje y detiene.
    Debe devolver el ID del spreadsheet (str) cuando se completa.
    """
    if _HAVE_EXT:
        return _run_evergreen(sc_service, drive, gsclient, site_url, params, dest_folder_id)
    _missing("run_evergreen")