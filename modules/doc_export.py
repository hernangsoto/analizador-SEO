# modules/doc_export.py

import os
import streamlit as st

def create_doc_from_template_with_content(
    credentials,
    title: str,
    analysis_text: str,
    dest_folder_id: str | None = None,
) -> str:
    """
    Crea un Google Doc (copiando un template si existe) y ESCRIBE 'analysis_text' al inicio.
    Devuelve el doc_id.
    - Busca template en st.secrets['docs']['template_id'] o en env DOC_TEMPLATE_ID.
    - Si no hay template, crea un Doc en blanco y luego mueve a la carpeta destino (si corresponde).
    """
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError

    # Normalizar texto
    analysis_text = (analysis_text or "").strip()
    if not analysis_text:
        analysis_text = "(Resumen no disponible)"

    # Branding opcional
    branding = None
    try:
        # Si tenés una constante con encabezado, usala. Si no, queda None.
        from modules.doc_export import PROMPT_BRANDING_NOMADIC as _BR
        branding = (_BR or "").strip()
    except Exception:
        branding = None

    full_text = ""
    if branding:
        full_text += branding.strip() + "\n\n"
    full_text += analysis_text.strip()
    # Link de referencia al Sheet (si lo agregaste antes fuera, no pasa nada por repetir)
    # Acá NO lo añadimos automáticamente para no duplicar. El caller ya lo concatena.

    drive = build("drive", "v3", credentials=credentials)
    docs = build("docs", "v1", credentials=credentials)

    # 1) Crear/copy del documento
    template_id = (
        (st.secrets.get("docs", {}) or {}).get("template_id")
        if hasattr(st, "secrets") else None
    ) or os.environ.get("DOC_TEMPLATE_ID")

    doc_id = None
    try:
        if template_id:
            meta = {"name": title}
            if dest_folder_id:
                meta["parents"] = [dest_folder_id]
            newfile = drive.files().copy(fileId=template_id, body=meta, fields="id").execute()
            doc_id = newfile["id"]
        else:
            # Crear documento vacío
            created = docs.documents().create(body={"title": title}).execute()
            doc_id = created["documentId"]
            # Mover a carpeta destino si se indicó
            if dest_folder_id:
                drive.files().update(fileId=doc_id, addParents=dest_folder_id, fields="id, parents").execute()
    except HttpError as e:
        raise RuntimeError(f"No se pudo crear/copiar el documento: {e}")

    # 2) Insertar contenido AL INICIO (index=1)
    #   En Google Docs, el cuerpo comienza en index 1. Insertar ahí lo pone al principio.
    try:
        requests = [
            {
                "insertText": {
                    "location": {"index": 1},
                    "text": full_text + "\n"
                }
            }
        ]
        docs.documents().batchUpdate(documentId=doc_id, body={"requests": requests}).execute()
    except HttpError as e:
        # El Doc quedó creado pero vacío: lo informamos con error claro
        raise RuntimeError(f"El documento se creó pero falló al escribir el contenido: {e}")

    return doc_id
