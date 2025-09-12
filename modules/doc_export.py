# modules/doc_export.py
from __future__ import annotations

import os
from typing import Optional
import streamlit as st

# Branding opcional que se antepone al contenido del documento
PROMPT_BRANDING_NOMADIC = "📝 Resumen generado con Nomadic BOT"

# ---- Helpers internos --------------------------------------------------------
def _get_services(credentials):
    """Devuelve clients de Drive y Docs usando las credenciales dadas."""
    from googleapiclient.discovery import build
    drive = build("drive", "v3", credentials=credentials)
    docs = build("docs", "v1", credentials=credentials)
    return drive, docs

def _copy_or_create_doc(credentials, title: str, dest_folder_id: Optional[str]) -> str:
    """
    Si hay template (secrets['docs']['template_id'] o env DOC_TEMPLATE_ID) lo copia;
    si no, crea un Doc vacío. Devuelve documentId.
    """
    drive, docs = _get_services(credentials)

    # Buscar template en secrets o env
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
        return newfile["id"]

    # Crear documento vacío
    created = docs.documents().create(body={"title": title}).execute()
    doc_id = created["documentId"]

    # Mover a carpeta destino (opcional). No removemos "root" para permitir múltiples padres.
    if dest_folder_id:
        try:
            drive.files().update(fileId=doc_id, addParents=dest_folder_id, fields="id,parents").execute()
        except Exception:
            pass
    return doc_id

def _insert_text_at_start(credentials, doc_id: str, text: str) -> None:
    """Inserta texto al comienzo del body (index=1)."""
    from googleapiclient.discovery import build
    docs = build("docs", "v1", credentials=credentials)
    requests = [{"insertText": {"location": {"index": 1}, "text": text}}]
    docs.documents().batchUpdate(documentId=doc_id, body={"requests": requests}).execute()

# ---- API pública -------------------------------------------------------------
def create_doc_from_template_with_content(
    credentials,
    title: str,
    analysis_text: str,
    dest_folder_id: Optional[str] = None,
) -> str:
    """
    Crea un Google Doc (copiando un template si existe) y ESCRIBE 'analysis_text' al inicio.
    Devuelve el doc_id.
    """
    analysis_text = (analysis_text or "").strip() or "(Resumen no disponible)"

    branding = (PROMPT_BRANDING_NOMADIC or "").strip()
    full_text = f"{branding}\n\n{analysis_text}" if branding else analysis_text

    doc_id = _copy_or_create_doc(credentials, title, dest_folder_id)
    _insert_text_at_start(credentials, doc_id, full_text + "\n")
    return doc_id

def create_doc_with_prompt(
    credentials,
    title: str,
    content: str,
    dest_folder_id: Optional[str] = None,
) -> str:
    """
    Compat: wrapper del viejo nombre que ahora simplemente escribe 'content'
    usando la misma lógica que create_doc_from_template_with_content.
    """
    return create_doc_from_template_with_content(credentials, title, content, dest_folder_id)
