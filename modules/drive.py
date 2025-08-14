# modules/drive.py
from __future__ import annotations

import time
from typing import List

import streamlit as st

from .utils import debug_log


# ========= Clientes y utilidades =========

def ensure_drive_clients(creds):
    """
    Devuelve clientes para Drive (v3) y gspread usando las credenciales personales.
    Hace imports perezosos y muestra errores claros si faltan dependencias.
    """
    try:
        from googleapiclient.discovery import build
    except Exception:
        st.error("Falta el paquete **google-api-python-client**.")
        st.caption("Agregá `google-api-python-client` a requirements.txt y redeploy.")
        raise

    try:
        import gspread
    except Exception:
        st.error("Falta el paquete **gspread**.")
        st.caption("Agregá `gspread` a requirements.txt y redeploy.")
        raise

    drive = build("drive", "v3", credentials=creds)
    gs = gspread.authorize(creds)
    return drive, gs


def get_google_identity(drive) -> dict:
    """Lee nombre y email de la cuenta autenticada en Google Drive."""
    try:
        me = drive.about().get(fields="user(displayName,emailAddress)").execute()
        return me.get("user", {})
    except Exception as e:
        debug_log("No pude leer identidad de Google Drive", str(e))
        return {}


def parse_drive_id_from_any(s: str | None) -> str | None:
    """Extrae ID de Drive desde una URL (archivo/carpeta) o devuelve s si parece ID."""
    if not s:
        return None
    s = s.strip()
    if "/folders/" in s:
        try:
            return s.split("/folders/")[1].split("?")[0].split("/")[0]
        except Exception:
            return None
    if "/d/" in s:
        try:
            return s.split("/d/")[1].split("/")[0]
        except Exception:
            return None
    if all(ch not in s for ch in ["/", "?", " "]) and len(s) >= 10:
        return s
    return None


def pick_destination(drive, identity: dict | None):
    """UI para elegir carpeta destino (opcional) en la cuenta personal conectada."""
    st.subheader("Destino de la copia (opcional)")
    me_email = (identity or {}).get("emailAddress")
    if me_email:
        st.caption(f"Se creará en la cuenta conectada: **{me_email}**. Si no elegís carpeta, irá a **Mi unidad**.")
    else:
        st.caption("Se creará en la cuenta conectada. Si no elegís carpeta, irá a Mi unidad.")

    folder_in = st.text_input(
        "Carpeta destino (URL o ID, opcional)",
        placeholder="https://drive.google.com/drive/folders/<FOLDER_ID> o ID",
        key="dest_folder_input",
    )
    folder_id = None
    folder_ok = False
    if folder_in:
        folder_id = parse_drive_id_from_any(folder_in)
        if not folder_id:
            st.error("No pude extraer un ID de carpeta de ese valor. Pegá la URL de la carpeta o el ID plano.")
        else:
            try:
                meta = (
                    drive.files()
                    .get(fileId=folder_id, fields="id,name,mimeType,driveId", supportsAllDrives=True)
                    .execute()
                )
                if meta.get("mimeType") == "application/vnd.google-apps.folder":
                    folder_ok = True
                    st.success(f"Usaremos la carpeta: **{meta.get('name','(sin nombre)')}**")
                    debug_log("Carpeta destino", meta)
                else:
                    st.error("El ID proporcionado no es una carpeta de Google Drive.")
            except Exception as e:
                st.error("No pude acceder a esa carpeta con esta cuenta. Verificá permisos.")
                debug_log("Error verificando carpeta destino", str(e))
    if folder_ok:
        st.session_state["dest_folder_id"] = folder_id
    return st.session_state.get("dest_folder_id")


# ========= Verificación y copia de template =========

def verify_template_access(drive, template_id: str) -> dict | None:
    """Comprueba acceso al template y devuelve metadatos; muestra errores útiles si falta habilitar APIs."""
    try:
        meta = (
            drive.files()
            .get(
                fileId=template_id,
                fields="id,name,parents,mimeType,owners(displayName,emailAddress),webViewLink,driveId",
                supportsAllDrives=True,
            )
            .execute()
        )
        if st.session_state.get("DEBUG"):
            try:
                perms = (
                    drive.permissions()
                    .list(fileId=template_id, fields="permissions(emailAddress,role,type)", supportsAllDrives=True)
                    .execute()
                )
                meta["_permissions"] = perms.get("permissions", [])
            except Exception as e:
                meta["_permissions_error"] = str(e)
        return meta
    except Exception as e:
        msg = str(e)
        if "accessNotConfigured" in msg or "has not been used in project" in msg:
            st.error("""La API de Google Drive **no está habilitada** en el proyecto de tu OAuth client.

➡️ Entrá a **Google Cloud Console** del proyecto de tu *client_id* y habilitá:
- **Google Drive API**
- **Google Sheets API**
- **Search Console API** (Webmasters)

Luego reintentá la autorización (Paso A y Paso B).""")
            st.caption("Tip: si tu app está en modo *Testing*, agregá tu email como *Test user* en la pantalla de consentimiento.")
            debug_log("HttpError accessNotConfigured", msg)
            st.stop()
        debug_log("HttpError al leer metadatos del template", msg)
        return None


def copy_template_and_open(drive, gsclient, template_id: str, title: str, dest_folder_id: str | None = None):
    """
    Copia el template en la cuenta personal y abre el Sheets con reintentos
    (evita planillas vacías por propagación).
    """
    meta = verify_template_access(drive, template_id)
    if not meta:
        raise RuntimeError(
            "No tengo acceso al template de Google Sheets especificado. Verificá que el ID sea correcto y que la cuenta autenticada tenga permiso."
        )

    owners = ", ".join([o.get("displayName") or o.get("emailAddress", "?") for o in meta.get("owners", [])]) or "(desconocido)"
    st.caption(f"Template detectado: **{meta.get('name','(sin nombre)')}** – Propietario(s): {owners}")
    debug_log("Metadatos del template", meta)

    body = {"name": title}
    if dest_folder_id:
        try:
            folder_meta = (
                drive.files()
                .get(fileId=dest_folder_id, fields="id,name,mimeType,driveId", supportsAllDrives=True)
                .execute()
            )
            if folder_meta.get("mimeType") != "application/vnd.google-apps.folder":
                raise RuntimeError("El ID de destino no es una carpeta de Google Drive.")
            body["parents"] = [dest_folder_id]
            st.caption(f"Destino: carpeta **{folder_meta.get('name','(sin nombre)')}**")
            debug_log("Destino carpeta", folder_meta)
        except Exception as e:
            debug_log("Error validando carpeta destino", str(e))
            raise RuntimeError("No tengo acceso a la carpeta destino con esta cuenta. Compartila o elegí otra.")
    else:
        st.caption("Destino: **Mi unidad** (raíz)")

    try:
        new_file = (
            drive.files()
            .copy(fileId=template_id, body=body, supportsAllDrives=True)
            .execute()
        )
        sid = new_file["id"]
    except Exception as e:
        raise RuntimeError(f"Falló la copia del template (ID={template_id}). Detalle: {e}")

    # Reintentos para que el archivo esté listo en Sheets
    last_err = None
    for i in range(7):  # ~3s total
        try:
            sh = gsclient.open_by_key(sid)
            _ = [ws.title for ws in sh.worksheets()]  # fuerza inicialización
            debug_log("Resultado de la copia", {"id": sid})
            return sh, sid
        except Exception as e:
            last_err = e
            time.sleep(0.3 + i * 0.4)

    raise RuntimeError(f"No pude abrir el nuevo Sheets (id={sid}). Detalle: {last_err}")


# ========= Helpers de escritura =========

def safe_set_df(ws, df, include_header=True):
    """
    Escribe el DataFrame manejando nulos y **redimensionando** la worksheet
    para evitar errores de API por límites del grid. Compatible con distintas
    versiones de gspread-dataframe (hace fallback si faltan kwargs).
    """
    try:
        from gspread_dataframe import set_with_dataframe
    except Exception:
        st.error("Falta el paquete **gspread-dataframe**.")
        st.caption("Agregá `gspread-dataframe` a requirements.txt y redeploy.")
        raise

    import pandas as pd

    # Normalizar DF (sin NaN) y evitar truthiness ambiguo
    if df is None:
        df = pd.DataFrame()
    else:
        df = df.copy()

    # Convertimos NaN a "", y dejamos números/fechas como están
    df = df.astype(object).where(pd.notnull(df), "")

    # Intento con firma completa; si la versión no acepta algún kw, hacemos fallback.
    try:
        set_with_dataframe(
            ws,
            df,
            include_column_header=include_header,
            resize=True,
            allow_formulas=True,
            string_ify=False,
        )
    except TypeError:
        try:
            set_with_dataframe(
                ws,
                df,
                include_column_header=include_header,
                resize=True,
                allow_formulas=True,
            )
        except TypeError:
            set_with_dataframe(
                ws,
                df,
                include_column_header=include_header,
                resize=True,
            )


def _ensure_ws(sheet, title: str):
    """Obtiene o crea la worksheet con ese título."""
    try:
        return sheet.worksheet(title)
    except Exception:
        return sheet.add_worksheet(title=title, rows=2000, cols=26)


# ========= Compartir / permisos =========

def _parse_emails_list(s: str) -> List[str]:
    if not s:
        return []
    parts = [p.strip() for p in s.replace(";", ",").split(",")]
    return [p for p in parts if p]


def grant_permissions(drive, file_id: str, emails: List[str], role: str = "reader", anyone_view: bool = False):
    """Asigna permisos al archivo recién creado."""
    ok, errs = [], []
    for em in emails:
        try:
            drive.permissions().create(
                fileId=file_id,
                body={"type": "user", "role": role, "emailAddress": em},
                sendNotificationEmail=False,
                supportsAllDrives=True,
                fields="id",
            ).execute()
            ok.append(em)
        except Exception as e:
            errs.append((em, str(e)))
    if anyone_view:
        try:
            drive.permissions().create(
                fileId=file_id,
                body={"type": "anyone", "role": "reader"},
                supportsAllDrives=True,
                fields="id",
            ).execute()
            ok.append("link: anyone (reader)")
        except Exception as e:
            errs.append(("anyone", str(e)))
    return ok, errs


def share_controls(drive, file_id: str, default_email: str | None = None):
    st.subheader("Compartir acceso al documento")
    st.caption("Podés abrir el link con la **misma cuenta** autenticada o compartirlo con otras direcciones.")
    emails_str = st.text_input(
        "Emails a compartir (separados por coma)",
        value=default_email or "",
        key=f"share_emails_{file_id}",
        placeholder="persona@ejemplo.com, otra@empresa.com",
    )
    role = st.selectbox("Rol", ["reader", "commenter", "writer"], index=0, key=f"share_role_{file_id}")
    anyone = st.checkbox("Cualquiera con el enlace (visor)", value=False, key=f"share_anyone_{file_id}")
    if st.button("Aplicar permisos", key=f"btn_share_{file_id}"):
        emails = _parse_emails_list(emails_str)
        ok, errs = grant_permissions(drive, file_id, emails, role=role, anyone_view=anyone)
        if ok:
            st.success("Permisos aplicados: " + ", ".join(ok))
        if errs:
            st.error("No se pudieron aplicar algunos permisos:")
            for em, err in errs:
                st.caption(f"• {em}: {err}")
