# modules/doc_export.py
from __future__ import annotations

from modules.utils import ensure_external_package

_ext = ensure_external_package()
if not _ext:
    raise ModuleNotFoundError(
        "No pude cargar el paquete externo `seo_analisis_ext` para doc_export. "
        "Revisá secrets['ext'] (token/repo_url/zip_url) y reiniciá."
    )

# 1) Preferir export directo desde __init__ (si existe)
create_doc_from_template_with_content = getattr(
    _ext, "create_doc_from_template_with_content", None
)

# 2) Si no está en __init__, intentar el submódulo
if create_doc_from_template_with_content is None:
    try:
        mod = __import__("seo_analisis_ext.doc_export", fromlist=["*"])
        create_doc_from_template_with_content = getattr(mod, "create_doc_from_template_with_content", None)
    except Exception:
        create_doc_from_template_with_content = None

if create_doc_from_template_with_content is None:
    raise ModuleNotFoundError(
        "El repo externo no expone `create_doc_from_template_with_content`. "
        "Agregalo en `seo_analisis_ext/doc_export.py` y/o reexportalo en `seo_analisis_ext/__init__.py`."
    )

__all__ = ["create_doc_from_template_with_content"]
