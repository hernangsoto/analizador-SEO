# modules/doc_export.py
from __future__ import annotations

import os
from typing import Optional

# Google APIs
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


# === Prompt corporativo (lo dejamos acá centralizado) ===
PROMPT_BRANDING_NOMADIC = """Quiero que actúes como un editor de documentos corporativos de agencia.
Vas a recibir un archivo de Word y tu tarea será:
1. Branding Nomadic
Inserta el logo de Nomadic en la esquina superior derecha de cada página. (https://nomadic.agency/wp-content/uploads/2021/03/logo-negro.png)
Respeta proporciones correctas, sin distorsión.
2. Tipografía y estilos
Aplica la tipografía Montserrat en todo el documento.
Usa la siguiente jerarquía:
H1 → tamaño 26, Montserrat, color negro.
H2 → tamaño 20, Montserrat, color negro.
H3 → tamaño 16, Montserrat, color negro.
Texto normal → tamaño 11, Montserrat Regular.
3. Tablas y datos
Estandariza todas las tablas con tipografía Montserrat, tamaño 10.
Centra el contenido de las celdas.
Aplica el color corporativo #5C3675 de fondo en la primera fila (encabezado) de cada tabla.
Usa texto en blanco y en negrita en los encabezados para contraste.
4. Revisión de coherencia
Corrige errores ortográficos o de formato.
Asegúrate de que los títulos, subtítulos y tablas mantengan consistencia visual.
Revisa márgenes, alineación y espaciado para lograr un diseño limpio y profesional.
5. Entrega
Devuelve un archivo Word con el formato aplicado.
El documento debe quedar listo para presentarse como material corporativo de Nomadic
"""


def _get_docs_service(credentials) -> object:
    """
    Construye el cliente de Google Docs API con las credenciales ya obtenidas.
    IMPORTANTE: asegurate de tener el scope de Docs en tu flujo OAuth:
      https://www.googleapis.com/auth/documents
    """
    return build("docs", "v1", credentials=credentials)


def _get_drive_service(credentials) -> object:
    # Ya lo usás en otros módulos, lo dejamos por si acá lo necesitás.
    return build("drive", "v3", credentials=credentials)


def create_doc_with_prompt(*, credentials, title: str, prompt_text: str, dest_folder_id: Optional[str] = None) -> str:
    """
    Crea un Google Doc con el texto del prompt (para que el equipo lo use como guía/brief).
    Devuelve el doc_id.
    """
    docs = _get_docs_service(credentials)
    drive = _get_drive_service(credentials)

    # 1) Crear un documento vacío
    doc = docs.documents().create(body={"title": title}).execute()
    doc_id = doc["documentId"]

    # 2) Mover a carpeta destino (si corresponde)
    if dest_folder_id:
        drive.files().update(
            fileId=doc_id,
            addParents=dest_folder_id,
            fields="id, parents",
            supportsAllDrives=True,
        ).execute()

    # 3) Insertar el texto del prompt al inicio
    try:
        requests = [
            {
                "insertText": {
                    "location": {"index": 1},
                    "text": prompt_text,
                }
            }
        ]
        docs.documents().batchUpdate(documentId=doc_id, body={"requests": requests}).execute()
    except HttpError as e:
        raise RuntimeError(f"Error al escribir prompt en Google Doc: {e}")

    return doc_id


# === (Opcional) Generar un Doc con el ANÁLISIS usando un template ===
def _get_nomadic_doc_template_id() -> Optional[str]:
    """
    Busca el template de Docs para formateo corporativo:
      - st.secrets['templates']['doc_nomadic']   (si usas Streamlit secrets)
      - env NOMADIC_DOC_TEMPLATE_ID
    """
    try:
        import streamlit as st  # noqa
        maybe = st.secrets.get("templates", {}).get("doc_nomadic")
        if isinstance(maybe, str) and maybe.strip():
            return maybe.strip()
    except Exception:
        pass
    env_tid = os.environ.get("NOMADIC_DOC_TEMPLATE_ID")
    return env_tid.strip() if env_tid else None


def create_doc_from_template_with_content(
    *,
    credentials,
    title: str,
    analysis_text: str,
    dest_folder_id: Optional[str] = None,
) -> str:
    """
    Copia un template de Google Docs (con header/logo/estilos Montserrat ya configurados),
    reemplaza el marcador {{CONTENT}} por el análisis y devuelve el doc_id.
    """
    docs = _get_docs_service(credentials)
    drive = _get_drive_service(credentials)
    template_id = _get_nomadic_doc_template_id()
    if not template_id:
        raise RuntimeError(
            "Falta el template de Docs. Define st.secrets['templates']['doc_nomadic'] o la env NOMADIC_DOC_TEMPLATE_ID."
        )

    # 1) Copiar el template
    body = {"name": title}
    if dest_folder_id:
        body["parents"] = [dest_folder_id]
    new_file = drive.files().copy(
        fileId=template_id,
        body=body,
        supportsAllDrives=True,
        fields="id",
    ).execute()
    doc_id = new_file["id"]

    # 2) Reemplazar marcador {{CONTENT}} por el análisis
    requests = [
        {
            "replaceAllText": {
                "containsText": {"text": "{{CONTENT}}", "matchCase": True},
                "replaceText": analysis_text,
            }
        }
    ]
    docs.documents().batchUpdate(documentId=doc_id, body={"requests": requests}).execute()
    return doc_id
