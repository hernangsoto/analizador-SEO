import streamlit as st
import requests
import pandas as pd
from datetime import datetime, date, timedelta
from urllib.parse import urlparse

# Google APIs
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
import gspread
from gspread_dataframe import set_with_dataframe

# --- OAuthlib settings para despliegues sin HTTPS en el redirect (localhost)
import os
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")  # permite http://localhost en el authorization_response
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")   # tolera diferencias de orden/espacios en scopes

# =============================
# Configuración base
# =============================
DEBUG_DEFAULT = bool(st.secrets.get("debug", False))
st.set_page_config(layout="wide", page_title="Análisis SEO", page_icon="📊")
st.title("Análisis SEO – GSC ➜ Google Sheets")

# Switch de depuración visible tras login
st.session_state.setdefault("DEBUG", DEBUG_DEFAULT)


# ---------------------------
# Helpers UI
# ---------------------------

def get_user():
    return getattr(st, "user", getattr(st, "experimental_user", None))

def get_first_name(full_name: str | None) -> str:
    if not full_name:
        return "👋"
    return full_name.split()[0]

def sidebar_user_info(user):
    with st.sidebar:
        with st.container():
            c1, c2 = st.columns([1, 3])
            with c1:
                if getattr(user, "picture", None):
                    try:
                        r = requests.get(user.picture, timeout=5)
                        if r.status_code == 200:
                            st.image(r.content, width=96)
                        else:
                            st.warning("No se pudo cargar la imagen.")
                    except Exception as e:
                        st.warning(f"Error al cargar la imagen: {e}")
                else:
                    st.info("Sin imagen de perfil.")
            with c2:
                st.header("Información del usuario", anchor=False)
                st.write(f"**Nombre:** {getattr(user, 'name', '—')}")
                st.write(f"**Correo:** {getattr(user, 'email', '—')}")
        st.divider()
        st.button(":material/logout: Cerrar sesión", on_click=st.logout, use_container_width=True)

# ---------------------------
# OAuth y clientes
# ---------------------------

SCOPES_DRIVE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
SCOPES_GSC = [
    "https://www.googleapis.com/auth/webmasters.readonly",
]

def build_flow(account_key: str, scopes: list[str]) -> Flow:
    """Crea un flujo OAuth2 a partir de st.secrets para la cuenta elegida, con scopes provistos."""
    try:
        acc = st.secrets["accounts"][account_key]
    except Exception:
        st.error("No encontré las credenciales en st.secrets['accounts'][…].")
        st.stop()
    client_secrets = {
        "installed": {
            "client_id": acc["client_id"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_secret": acc["client_secret"],
            "redirect_uris": ["http://localhost"],
        }
    }
    flow = Flow.from_client_config(client_secrets, scopes=scopes)
    flow.redirect_uri = "http://localhost"
    return flow

def ensure_drive_clients(creds: Credentials):
    drive = build("drive", "v3", credentials=creds)
    gs = gspread.authorize(creds)
    return drive, gs


def ensure_sc_client(creds: Credentials):
    sc = build("searchconsole", "v1", credentials=creds)
    return sc


def debug_log(msg: str, data: dict | str | None = None):
    """Log simple en modo debug (muestra bloques legibles en la app)."""
    if st.session_state.get("DEBUG"):
        st.info(msg)
        if data is not None:
            try:
                import json
                st.code(json.dumps(data, indent=2, ensure_ascii=False))
            except Exception:
                st.code(str(data))


def get_google_identity(drive) -> dict:
    """Devuelve displayName y email de la cuenta de Google autenticada."""
    try:
        me = drive.about().get(fields="user(displayName,emailAddress)").execute()
        return me.get("user", {})
    except Exception as e:
        debug_log("No pude leer identidad de Google Drive", str(e))
        return {}

def parse_drive_id_from_any(s: str | None) -> str | None:
    """Extrae un ID de Drive desde URL o lo devuelve si ya es un ID.
    Acepta URLs de archivo (/d/<ID>) o de carpeta (/folders/<ID>)."""
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
    # ID plano (heurística simple)
    if all(ch not in s for ch in ["/", "?", " "]) and len(s) >= 10:
        return s
    return None


def pick_destination(drive, identity: dict | None):
    """UI para elegir carpeta destino opcional (en la CUENTA autenticada)."""
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
            st.error("No pude extraer un ID de carpeta de ese valor. Probá pegar la URL de la carpeta o el ID plano.")
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

# ---------------------------
# Funciones GSC (adaptadas del Colab)
# ---------------------------

def _fetch_all_rows(service, site_url, body, page_size=25000):
    all_rows, start = [], 0
    while True:
        page_body = dict(body)
        page_body["rowLimit"] = page_size
        if start:
            page_body["startRow"] = start
        resp = service.searchanalytics().query(siteUrl=site_url, body=page_body).execute()
        batch = resp.get("rows", [])
        if not batch:
            break
        all_rows.extend(batch)
        if len(batch) < page_size:
            break
        start += page_size
    return all_rows

def consultar_datos(service, site_url, fecha_inicio, fecha_fin, tipo_dato, pais=None, seccion_filtro=None):
    seccion_frag = seccion_filtro.strip("/") if seccion_filtro else None
    body = {"startDate": str(fecha_inicio), "endDate": str(fecha_fin), "dimensions": ["page"]}
    body["type"] = "discover" if tipo_dato == "discover" else "web"
    if tipo_dato == "discover":
        body["dataState"] = "all"
    filters = []
    if seccion_frag:
        filters.append({"dimension": "page", "operator": "contains", "expression": f"/{seccion_frag}"})
    if pais:
        filters.append({"dimension": "country", "operator": "equals", "expression": pais})
    if filters:
        body["dimensionFilterGroups"] = [{"filters": filters}]
    rows = _fetch_all_rows(service, site_url, body)
    if not rows:
        return pd.DataFrame(columns=["url", "clicks", "impressions", "ctr", "position"])
    df = pd.DataFrame([
        {
            "url": r["keys"][0],
            "clicks": r.get("clicks", 0),
            "impressions": r.get("impressions", 0),
            "ctr": r.get("ctr", 0.0),
            "position": r.get("position", 0.0),
        }
        for r in rows
    ])
    return df

def consultar_por_pais(service, site_url, fecha_inicio, fecha_fin, tipo_dato, seccion_filtro=None):
    seccion_frag = seccion_filtro.strip("/") if seccion_filtro else None
    body = {"startDate": str(fecha_inicio), "endDate": str(fecha_fin), "dimensions": ["country"]}
    body["type"] = "discover" if tipo_dato == "discover" else "web"
    if tipo_dato == "discover":
        body["dataState"] = "all"
    filters = []
    if seccion_frag:
        filters.append({"dimension": "page", "operator": "contains", "expression": f"/{seccion_frag}"})
    if filters:
        body["dimensionFilterGroups"] = [{"filters": filters}]
    rows = _fetch_all_rows(service, site_url, body, page_size=250)
    if not rows:
        return pd.DataFrame(columns=["country", "clicks", "impressions"])
    df = pd.DataFrame([
        {"country": r.get("keys", [None])[0], "clicks": r.get("clicks", 0), "impressions": r.get("impressions", 0)}
        for r in rows
    ])
    return df.groupby("country", as_index=False)[["clicks", "impressions"]].sum().sort_values("clicks", ascending=False)

# ---------------------------
# Exportar a Google Sheets
# ---------------------------

def get_template_id(kind: str, account_key: str | None = None) -> str | None:
    """Obtiene el template ID desde secrets. Admite por cuenta y global.
    Prioridad: templates[<account_key>][<kind>] -> templates[<kind>]
    """
    troot = st.secrets.get("templates", {})
    if account_key and isinstance(troot.get(account_key), dict):
        return troot[account_key].get(kind) or troot.get(kind)
    return troot.get(kind)


def verify_template_access(drive, template_id: str) -> dict | None:
    """Lee metadatos (y permisos si DEBUG) del template para verificar acceso.
    Si la API de Drive no está habilitada en el proyecto de tu client_id,
    muestra un mensaje claro para habilitarla.
    """
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
    except HttpError as e:
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
    except Exception as e:
        debug_log("Error al leer metadatos del template", str(e))
        return None


def copy_template_and_open(drive, gsclient, template_id: str, title: str, dest_folder_id: str | None = None):
    # Pre-chequeo de acceso para dar mensajes claros
    meta = verify_template_access(drive, template_id)
    if not meta:
        raise RuntimeError(
            "No tengo acceso al template de Google Sheets especificado. Verificá que el ID sea correcto y que la cuenta de Google autenticada tenga permiso de lectura."
        )
    # Mostrar info útil para depurar (no sensible)
    owners = ", ".join([o.get("displayName") or o.get("emailAddress", "?") for o in meta.get("owners", [])]) or "(desconocido)"
    st.caption(f"Template detectado: **{meta.get('name','(sin nombre)')}** – Propietario(s): {owners}")
    debug_log("Metadatos del template", meta)

    # Armar body de copia; si se eligió carpeta, validarla y usarla como parent
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
        debug_log("Resultado de la copia", new_file)
        sid = new_file["id"]
        view_link = f"https://docs.google.com/spreadsheets/d/{sid}"
        debug_log("Link del nuevo archivo", {"webViewLink": view_link})
        sheet = gsclient.open_by_key(sid)
        return sheet, sid
    except Exception as e:
        debug_log("Excepción al copiar template", str(e))
        raise RuntimeError(f"Falló la copia del template (ID={template_id}). Detalle: {e}")

def safe_set_df(ws, df: pd.DataFrame | None, include_header=True):
    # Evitar 'df or pd.DataFrame()' porque la verdad de un DataFrame es ambigua en pandas
    if df is None:
        df = pd.DataFrame()
    else:
        df = df.copy()
    df = df.astype(object).where(pd.notnull(df), "")
    ws.clear()
    set_with_dataframe(ws, df, include_column_header=include_header)

# ---------------------------
# Vistas / UI principal
# ---------------------------

LAG_DAYS_DEFAULT = 3

def login_screen():
    st.header("Esta aplicación es privada.")
    st.subheader("Por favor, inicia sesión.")
    st.button(":material/login: Iniciar sesión con Google", on_click=st.login)

# PATCH: OAuth con Flow persistido + authorization_response (URL completa) y sin include_granted_scopes

def pick_destination_oauth():
    """OAuth para la cuenta PERSONAL (destino de Drive/Sheets). Reutiliza tokens si ya existen en caché.
    """
    st.subheader("1) Conectar Google PERSONAL (Drive/Sheets)")

    # Reutilizar credenciales si ya existen para el email actual de Streamlit
    user_email = getattr(get_user(), "email", None)
    cache = st.session_state.setdefault("creds_cache", {"dest": {}, "src": {}})

    if user_email and cache["dest"].get(user_email):
        cached = cache["dest"][user_email]
        creds = Credentials(**cached)
        creds = refresh_if_needed(creds)
        if creds and creds.valid:
            st.success(f"Cuenta PERSONAL conectada (caché): {user_email}")
            if st.button("Cambiar cuenta PERSONAL"):
                cache["dest"].pop(user_email, None)
                st.session_state.pop("creds_dest", None)
                st.session_state.pop("oauth_dest", None)
                st.experimental_rerun()
            st.session_state["creds_dest"] = creds_to_dict(creds)
            return creds

    acct_for_dest = st.secrets.get("oauth_app_key", "ACCESO")
    if st.session_state.get("oauth_dest", {}).get("account_key") != acct_for_dest:
        st.session_state.pop("oauth_dest", None)

    if "oauth_dest" not in st.session_state:
        flow = build_flow(acct_for_dest, SCOPES_DRIVE)
        auth_url, state = flow.authorization_url(
            prompt="select_account",  # no forzamos consent cada vez
            access_type="offline",
            include_granted_scopes="false",
        )
        st.session_state["oauth_dest"] = {
            "account_key": acct_for_dest,
            "flow": flow,
            "auth_url": auth_url,
            "state": state,
        }
    od = st.session_state["oauth_dest"]
    st.markdown(f"🔗 **Paso A (personal):** [Autorizar acceso de Drive/Sheets]({od['auth_url']})")
    with st.expander("Ver/copiar URL de autorización (personal)"):
        st.code(od["auth_url"])
    url = st.text_input(
        "🔑 Paso B (personal): pegá la URL completa (http://localhost/?code=...&state=...)",
        key="auth_response_url_dest",
        placeholder="http://localhost/?code=...&state=...",
    )
    creds = None
    if st.button("Conectar Google PERSONAL", type="primary"):
        if not url.strip():
            st.error("Pegá la URL completa de redirección (incluye code y state).")
            st.stop()
        try:
            flow: Flow = od["flow"]
            flow.fetch_token(authorization_response=url.strip())
            creds = flow.credentials
            st.session_state["creds_dest"] = creds_to_dict(creds)
            # Guardar en caché por email de Streamlit
            if user_email:
                cache["dest"][user_email] = st.session_state["creds_dest"]
            st.success("Cuenta PERSONAL conectada.")
        except Exception as e:
            st.session_state.pop("oauth_dest", None)
            st.error("No se pudo conectar la cuenta PERSONAL. Reintentá autorización y pegá la URL completa.")
            st.caption(f"Detalle técnico (debug): {e}")
    if not creds and st.session_state.get("creds_src"):
        creds = Credentials(**st.session_state["creds_src"])
        creds = refresh_if_needed(creds)
    return creds

