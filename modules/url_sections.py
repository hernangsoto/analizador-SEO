# modules/url_sections.py
from __future__ import annotations
from urllib.parse import urlparse

# Segmentos iniciales que NO son sección (p. ej. /amp/... )
IGNORED_FIRST_SEGMENTS = {"amp"}

def _split_path(url_or_path: str) -> list[str]:
    """Devuelve los segmentos limpios del path, sin prefijos ignorados."""
    if not url_or_path:
        return []
    p = urlparse(url_or_path)
    path = p.path or url_or_path  # admite recibir ruta directa
    parts = [seg for seg in path.split("/") if seg]

    # Ignorar prefijos como /amp/
    while parts and parts[0].lower() in IGNORED_FIRST_SEGMENTS:
        parts.pop(0)
    return parts

def extract_section_subsection(url_or_path: str) -> tuple[str | None, str | None]:
    """
    Regla:
    - Sección = primer segmento (ignorando prefijos como /amp/)
    - Subsección = segundo segmento SOLO si hay al menos 3 segmentos (sección / subsección / slug)
      En rutas del tipo /sección/slug.html NO hay subsección.
    """
    parts = _split_path(url_or_path)
    if not parts:
        return None, None

    section = parts[0].lower()

    # subsección solo si hay al menos 3 niveles: /seccion/subseccion/slug(.html)
    subsection = parts[1].lower() if len(parts) >= 3 else None
    return section, subsection