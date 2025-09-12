# modules/doc_export.py
from __future__ import annotations
import os
import uuid
import datetime as _dt
from googleapiclient.discovery import build

def _get_template_id():
    tid = os.environ.get("DOC_TEMPLATE_ID")
    try:
        import streamlit as st  # opcional: si estás en Streamlit
        tid = tid or (st.secrets.get("docs", {}).get("template_id"))
    except Exception:
        pass
    if not tid:
        raise RuntimeError("Falta template_id: define DOC_TEMPLATE_ID o secrets['docs']['template_id'].")
    return tid

def create_doc_from_template_with_content(
    credentials,
    title: str,
    analysis_text: str,
    dest_folder_id: str | None = None,
    template_id: str | None = None,
    extra_replacements: dict[str, str] | None = None,
) -> str:
    """
    Crea un Doc desde template y pega analysis_text en {{CONTENT}} preservando estilo del template.
    - Si el párrafo de {{CONTENT}} era una lista, se aplica bullet a todo el bloque insertado.
    - Reemplaza también {{TITLE}}, {{DATE}} y cualquier clave en extra_replacements.
    Devuelve doc_id.
    """
    template_id = template_id or _get_template_id()

    drive = build("drive", "v3", credentials=credentials)
    docs  = build("docs",  "v1", credentials=credentials)

    # 1) Copia del template + nombre final
    copy_body = {"name": title}
    if dest_folder_id:
        copy_body["parents"] = [dest_folder_id]
    newf = drive.files().copy(fileId=template_id, body=copy_body, fields="id,name").execute()
    doc_id = newf["id"]

    # 2) Reemplazos simples (menos {{CONTENT}})
    today = _dt.date.today().strftime("%Y-%m-%d")
    reps = {"{{TITLE}}": title, "{{DATE}}": today}
    if extra_replacements:
        reps.update(extra_replacements)

    reqs = []
    for k, v in reps.items():
        if k == "{{CONTENT}}":
            continue
        reqs.append({
            "replaceAllText": {
                "containsText": {"text": k, "matchCase": True},
                "replaceText": v
            }
        })

    # 3) Marcador temporal para {{CONTENT}}
    marker = f"<<CONTENT_{uuid.uuid4().hex[:8]}>>"
    reqs.append({
        "replaceAllText": {
            "containsText": {"text": "{{CONTENT}}", "matchCase": True},
            "replaceText": marker
        }
    })

    if reqs:
        docs.documents().batchUpdate(documentId=doc_id, body={"requests": reqs}).execute()

    # 4) Buscar el marcador con índices y reemplazarlo por el texto real
    doc = docs.documents().get(documentId=doc_id).execute()

    def _find_marker_range(document, needle: str):
        body = document.get("body", {})
        for el in body.get("content", []):
            para = el.get("paragraph")
            if not para:
                continue
            elements = para.get("elements", [])
            for e in elements:
                tr = e.get("textRun")
                if not tr:
                    continue
                txt = tr.get("content", "") or ""
                pos = txt.find(needle)
                if pos != -1:
                    start = e["startIndex"] + pos
                    end   = start + len(needle)
                    is_bulleted = bool(para.get("bullet"))
                    return start, end, is_bulleted
        return None, None, False

    start, end, is_bulleted = _find_marker_range(doc, marker)
    if start is None:
        # No se encontró marcador; devolvemos tal cual
        return doc_id

    # 5) Borrar marcador e insertar el contenido
    req2 = [
        {"deleteContentRange": {"range": {"startIndex": start, "endIndex": end}}},
        {"insertText": {"location": {"index": start}, "text": analysis_text}},
    ]

    # 6) Si el párrafo original era una lista, aplicar bullets a TODOS los párrafos insertados
    end_after = start + len(analysis_text)
    if is_bulleted:
        req2.append({
            "createParagraphBullets": {
                "range": {"startIndex": start, "endIndex": end_after},
                "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE"
            }
        })

    docs.documents().batchUpdate(documentId=doc_id, body={"requests": req2}).execute()
    return doc_id
