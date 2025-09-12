# modules/doc_export.py
from __future__ import annotations

import os
from typing import Optional, Tuple
import streamlit as st

# Branding opcional que se antepone al contenido del documento
PROMPT_BRANDING_NOMADIC = "📝 Resumen generado con Nomadic BOT"

# ---------------- Helpers internos ----------------
def _get_services(credentials):
    """Devuelve clients de Drive y Docs usando las credenciales dadas."""
    from googleapiclient.discovery import build
    drive = build("drive", "v3", credentials=credentials)
    docs = build("docs", "v1", credentials=credentials)
    return drive, docs

def _get_placeholder_token() -> str:
    try:
        val = (st.secrets.get("docs", {}) or {}).get("placeholder")
        if val:
            return str(val)
    except Exception:
        pass
    return os.environ.get("DOC_TEMPLATE_PLACEHOLDER", "{{CONTENT}}")

def _copy_or_create_doc(credentials, title: str, dest_folder_id: Optional[str]) -> Tuple[str, bool]:
    """
    Copia template si existe (secrets['docs']['template_id'] o env DOC_TEMPLATE_ID),
    si no, crea un Doc vacío.
    Devuelve (documentId, used_template: bool).
    """
    drive, docs = _get_services(credentials)
    # Buscar template
    template_id = None
    try:
        template_id = (st.secrets.get("docs", {}) or {}).get("template_id")
    except Exception:
        template_id = None
    if not template_id:
        template_id = os.environ.get("DOC_TEMPLATE_ID")

    if template_id:
        body = {"name": title}
        if dest_folder_id:
            body["parents"] = [dest_folder_id]
        newfile = drive.files().copy(fileId=template_id, body=body, fields="id").execute()
        return newfile["id"], True

    # Crear documento vacío
    created = docs.documents().create(body={"title": title}).execute()
    doc_id = created["documentId"]

    # Mover a carpeta destino (opcional)
    if dest_folder_id:
        try:
            drive.files().update(fileId=doc_id, addParents=dest_folder_id, fields="id,parents").execute()
        except Exception:
            pass
    return doc_id, False

def _insert_text_at_start(credentials, doc_id: str, text: str) -> None:
    """Inserta texto al comienzo del body (index=1)."""
    from googleapiclient.discovery import build
    docs = build("docs", "v1", credentials=credentials)
    requests = [{"insertText": {"location": {"index": 1}, "text": text}}]
    docs.documents().batchUpdate(documentId=doc_id, body={"requests": requests}).execute()

def _find_placeholder_indices(credentials, doc_id: str, placeholder: str) -> Optional[Tuple[int, int]]:
    """Devuelve (startIndex, endIndex) de la PRIMERA aparición del placeholder en el Doc."""
    from googleapiclient.discovery import build
    docs = build("docs", "v1", credentials=credentials)
    doc = docs.documents().get(documentId=doc_id, fields="body(content)").execute()
    content = (doc.get("body") or {}).get("content") or []
    for elem in content:
        para = elem.get("paragraph")
        if not para:
            continue
        for el in para.get("elements", []):
            tr = el.get("textRun")
            if not tr:
                continue
            txt = tr.get("content") or ""
            if not txt:
                continue
            idx = txt.find(placeholder)
            if idx >= 0:
                start_index = el.get("startIndex", 1) + idx
                end_index = start_index + len(placeholder)
                return start_index, end_index
    return None

def _replace_placeholder_with_text(credentials, doc_id: str, placeholder: str, text: str) -> bool:
    """
    Reemplaza el 'placeholder' por 'text' manteniendo el estilo del párrafo del template.
    Devuelve True si pudo reemplazar, False si no se encontró placeholder.
    """
    from googleapiclient.discovery import build
    docs = build("docs", "v1", credentials=credentials)

    # 1) Buscar indices del placeholder
    rng = _find_placeholder_indices(credentials, doc_id, placeholder)
    if not rng:
        return False
    start, end = rng

    # 2) Borrar placeholder e insertar texto en su lugar
    requests = [
        {"deleteContentRange": {"range": {"startIndex": start, "endIndex": end}}},
        {"insertText": {"location": {"index": start}, "text": text}},
    ]
    docs.documents().batchUpdate(documentId=doc_id, body={"requests": requests}).execute()
    return True

# ---------------- API pública ----------------
def create_doc_from_template_with_content(
    credentials,
    title: str,
    analysis_text: str,
    dest_folder_id: Optional[str] = None,
) -> str:
    """
    Crea un Google Doc (copiando un template si existe) y ESCRIBE 'analysis_text' en el placeholder
    (por defecto {{CONTENT}}) para heredar el formato del template. Si no hay template/placeholder,
    inserta el contenido al inicio del documento.
    Devuelve el doc_id.
    """
    analysis_text = (analysis_text or "").strip() or "(Resumen no disponible)"

    branding = (PROMPT_BRANDING_NOMADIC or "").strip()
    full_text = f"{branding}\n\n{analysis_text}" if branding else analysis_text
    # Asegurar salto de línea final (evita pegarse al siguiente contenido del Doc)
    full_text = full_text.rstrip() + "\n"

    doc_id, used_template = _copy_or_create_doc(credentials, title, dest_folder_id)
    placeholder = _get_placeholder_token() if used_template else None

    if placeholder:
        replaced = _replace_placeholder_with_text(credentials, doc_id, placeholder, full_text)
        if not replaced:
            # Fallback si el template no tiene el placeholder
            _insert_text_at_start(credentials, doc_id, full_text)
    else:
        _insert_text_at_start(credentials, doc_id, full_text)

    return doc_id

def create_doc_with_prompt(
    credentials,
    title: str,
    content: str,
    dest_folder_id: Optional[str] = None,
) -> str:
    """Compat: alias del viejo nombre."""
    return create_doc_from_template_with_content(credentials, title, content, dest_folder_id)
