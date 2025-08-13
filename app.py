import streamlit as st
import pandas as pd
from datetime import date, datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import gspread
from gspread_dataframe import set_with_dataframe
import time
import json
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials

# -------------------------------------------------------------
# 🔐 Autenticación (Service Account desde .streamlit/secrets.toml)
# -------------------------------------------------------------
# En secrets.toml debes tener la clave JSON completa en:
# [gcp_service_account]
# type = "service_account"
# ...
# Además, comparte tu sitio en Search Console y el Template de Google Sheets
# con el email de la service account (st.secrets["gcp_service_account"]["client_email"]).
# -------------------------------------------------------------

SCOPES = [
    "https://www.googleapis.com/auth/webmasters.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

def get_credentials(sa_info: dict):
    creds = service_account.Credentials.from_service_account_info(
        sa_info, scopes=SCOPES
    )
    return creds

def get_services(creds):
    gsc = build("searchconsole", "v1", credentials=creds)
    drive = build("drive", "v3", credentials=creds)
    gs_client = gspread.authorize(creds)
    return gsc, drive, gs_client

# -------------------------------------------------------------
# 🔧 Utilidades
# -------------------------------------------------------------

def list_verified_sites(gsc_service):
    """Devuelve lista de sitios verificados (donde no sea unverified)."""
    try:
        resp = gsc_service.sites().list().execute() or {}
        entries = resp.get("siteEntry", [])
        sites = [e["siteUrl"] for e in entries if e.get("permissionLevel") != "siteUnverifiedUser"]
        return sorted(sites)
    except Exception as e:
        st.error(f"No pude listar sitios: {e}")
        return []


def _fetch_all_rows(gsc_service, site_url: str, body: dict, page_size: int = 25000):
    """Paginación segura para Search Console API."""
    all_rows, start = [], 0
    while True:
        page_body = dict(body)
        page_body["rowLimit"] = page_size
        if start:
            page_body["startRow"] = start
        resp = gsc_service.searchanalytics().query(siteUrl=site_url, body=page_body).execute()
        rows = resp.get("rows", [])
        if not rows:
            break
        all_rows.extend(rows)
        if len(rows) < page_size:
            break
        start += page_size
    return all_rows


def fetch_site_daily_totals(gsc_service, site_url: str, start_dt: date, end_dt: date,
                             tipo: str, country_iso3: str | None, section_path: str | None) -> pd.DataFrame:
    """Devuelve clicks/impressions diarios del *sitio* para Web o Discover."""
    body = {
        "startDate": str(start_dt),
        "endDate": str(end_dt),
        "dimensions": ["date"],
        "type": "discover" if tipo == "Discover" else "web",
    }
    if tipo == "Discover":
        body["dataState"] = "all"
    filters = []
    if country_iso3:
        filters.append({"dimension": "country", "operator": "equals", "expression": country_iso3})
    if section_path:
        # Aceptamos "/vida/" o "vida"; normalizamos para contains con slash
        frag = section_path.strip("/")
        filters.append({"dimension": "page", "operator": "contains", "expression": f"/{frag}"})
    if filters:
        body["dimensionFilterGroups"] = [{"filters": filters}]

    rows = _fetch_all_rows(gsc_service, site_url, body, page_size=5000)
    df = (
        pd.DataFrame([
            {
                "Fecha": pd.to_datetime(r["keys"][0]).date(),
                "Clicks": r.get("clicks", 0),
                "Impresiones": r.get("impressions", 0),
            }
            for r in rows
        ]) if rows else pd.DataFrame(columns=["Fecha", "Clicks", "Impresiones"]) 
    )
    df = df.sort_values("Fecha").reset_index(drop=True)
    return df


def ensure_worksheet(gc: gspread.Client, spreadsheet_key: str, sheet_title: str):
    """Asegura la hoja y la limpia (preservando formato del template)."""
    sh = gc.open_by_key(spreadsheet_key)
    try:
        ws = sh.worksheet(sheet_title)
        ws.clear()  # limpia valores (el formato del template suele quedar)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=sheet_title, rows=2000, cols=20)
    return sh, ws


def write_df_to_sheet(ws: gspread.Worksheet, df: pd.DataFrame):
    # Normalizar fecha a texto YYYY-MM-DD (sin 00:00:00)
    out = df.copy()
    if "Fecha" in out.columns:
        out["Fecha"] = pd.to_datetime(out["Fecha"]).dt.date.astype(str)
    set_with_dataframe(ws, out, include_index=False, include_column_header=True, resize=True)


def copy_template_sheet(drive_service, template_id: str, new_title: str) -> str:
    """Copia el template en tu Drive y devuelve el fileId nuevo."""
    body = {"name": new_title}
    new_file = drive_service.files().copy(fileId=template_id, body=body).execute()
    return new_file["id"]

# -------------------------------------------------------------
# 🖥️ UI
# -------------------------------------------------------------

st.set_page_config(page_title="Analizador de Medios — Streamlit", layout="wide")
st.title("Analizador de Medios (Streamlit)")
st.caption("Autenticación con OAuth (usuario) o Service Account. Exporta datos diarios a un Google Sheet basado en un template.")

with st.sidebar:
    st.subheader("Autenticación")
    auth_method = st.radio("Método", ["OAuth (usuario)", "Service Account"], index=0)

    oauth_creds = None
    sa_info = None

    if auth_method == "OAuth (usuario)":
        # Intentar cargar config desde secrets anidado: [gcp_oauth_client] -> [web]
        oauth_cfg = None
        try:
            oauth_cfg = dict(st.secrets["gcp_oauth_client"]["web"])  # TOML anidado correcto
        except Exception:
            pass
        # Fallback manual si no hay secrets
        if not oauth_cfg:
            st.warning("No encontré gcp_oauth_client → web en Secrets. Pegá los datos para probar.")
            col1, col2 = st.columns(2)
            with col1:
                cid = st.text_input("client_id")
                csecret = st.text_input("client_secret", type="password")
            with col2:
                redirect_uri = st.text_input("redirect_uri", value="https://hernangsoto.streamlit.app/")
            if cid and csecret and redirect_uri:
                oauth_cfg = {
                    "client_id": cid,
                    "client_secret": csecret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [redirect_uri],
                }
        if oauth_cfg:
            st.success("Config OAuth cargada.")
            redirect_uri = oauth_cfg["redirect_uris"][0]
            # Si ya está logueado
            if st.session_state.get("oauth_creds"):
                oauth_creds = st.session_state.get("oauth_creds")
                st.info("Sesión iniciada con Google.")
                if st.button("Cerrar sesión"):
                    st.session_state["oauth_creds"] = None
                    st.rerun()
            else:
                # Generar URL de login
                flow = Flow.from_client_config(
                    oauth_cfg if "web" in oauth_cfg else {"web": oauth_cfg},
                    scopes=[
                        "https://www.googleapis.com/auth/webmasters.readonly",
                        "https://www.googleapis.com/auth/spreadsheets",
                        "https://www.googleapis.com/auth/drive",
                    ],
                )
                flow.redirect_uri = redirect_uri
                if st.button("🔐 Iniciar sesión con Google"):
                    auth_url, state = flow.authorization_url(
                        access_type="offline", include_granted_scopes="true", prompt="consent"
                    )
                    st.session_state["_oauth_cfg"] = oauth_cfg
                    st.session_state["_redirect_uri"] = redirect_uri
                    st.session_state["_oauth_state"] = state
                    st.session_state["_auth_url"] = auth_url
                if st.session_state.get("_auth_url"):
                    st.link_button("Abrir Google Login", st.session_state["_auth_url"]) 

        # Manejar callback
        qp = {}
        try:
            qp = dict(st.query_params)
        except Exception:
            pass
        if (not oauth_creds) and qp.get("code") and st.session_state.get("_oauth_cfg"):
            code = qp["code"][0] if isinstance(qp.get("code"), list) else qp.get("code")
            flow2 = Flow.from_client_config(
                st.session_state["_oauth_cfg"] if "web" in st.session_state["_oauth_cfg"] else {"web": st.session_state["_oauth_cfg"]},
                scopes=[
                    "https://www.googleapis.com/auth/webmasters.readonly",
                    "https://www.googleapis.com/auth/spreadsheets",
                    "https://www.googleapis.com/auth/drive",
                ],
            )
            flow2.redirect_uri = st.session_state["_redirect_uri"]
            # Validar state opcionalmente
            if st.session_state.get("_oauth_state") and qp.get("state"):
                state_back = qp["state"][0] if isinstance(qp.get("state"), list) else qp.get("state")
                if state_back != st.session_state.get("_oauth_state"):
                    st.error("Estado OAuth inválido. Reintentá.")
                    st.stop()
            try:
                flow2.fetch_token(code=code)
                oauth_creds = flow2.credentials
                st.session_state["oauth_creds"] = oauth_creds
                st.success("Login correcto ✅")
            except Exception as e:
                st.error(f"No pude completar el login OAuth: {e}")
    else:
        # Service Account (fallback)
        if "gcp_service_account" in st.secrets:
            sa_info = dict(st.secrets["gcp_service_account"])
            st.success(f"Service Account: {sa_info.get('client_email', '(sin email)')}")
        else:
            st.warning("No encontré gcp_service_account en secrets. Subí el JSON o pegalo abajo.")
            uploaded = st.file_uploader("Subí el JSON de la Service Account", type=["json"])
            pasted = st.text_area("…o pegá el JSON aquí")
            if uploaded:
                try:
                    sa_info = json.load(uploaded)
                    st.success(f"Service Account: {sa_info.get('client_email', '(sin email)')}")
                except Exception as e:
                    st.error(f"JSON inválido: {e}")
            elif pasted.strip():
                try:
                    sa_info = json.loads(pasted)
                    st.success(f"Service Account: {sa_info.get('client_email', '(sin email)')}")
                except Exception as e:
                    st.error(f"JSON inválido: {e}")
# Servicios
# Determinar credenciales a usar
creds = None
if 'auth_method' in locals() and auth_method == "OAuth (usuario)":
    if oauth_creds is None:
        st.stop()
    creds = oauth_creds
else:
    if not sa_info:
        st.stop()
    creds = get_credentials(sa_info)

# Servicios
with st.spinner("Inicializando servicios de Google…"):
    gsc_service, drive_service, gs_client = get_services(creds)

# Sitio
sites = list_verified_sites(gsc_service)
colA, colB = st.columns([2, 1])
with colA:
    site_url = st.selectbox("Sitio verificado en Search Console", sites, placeholder="Elegí un sitio")
with colB:
    st.write("")
    st.write("")
    st.link_button("Abrir Search Console", "https://search.google.com/search-console", disabled=not bool(site_url))

# Parámetros comunes
st.markdown("---")
st.subheader("Parámetros del análisis")
col1, col2, col3 = st.columns(3)
with col1:
    start_dt = st.date_input("Desde", value=date.today().replace(day=1))
with col2:
    end_dt = st.date_input("Hasta", value=date.today())
with col3:
    country = st.text_input("País (ISO-3 opcional, ej: ARG, USA)").strip().upper() or None

col4, col5 = st.columns(2)
with col4:
    section = st.text_input("Sección (opcional, ej: /vida/)").strip() or None
with col5:
    tipos = st.multiselect("Tipo de tráfico", ["Search", "Discover"], default=["Search", "Discover"]) 

st.markdown("---")
st.subheader("Google Sheets — Template y salida")
TEMPLATE_ID_DEFAULT = "1dTGsOyJHWZlvONQa_9WuiaXBDLPCif4A7hE0gSPZcBU"
col6, col7 = st.columns([2, 1])
with col6:
    template_id = st.text_input("ID del Template de Sheets", value=TEMPLATE_ID_DEFAULT)
    suggested_title = f"{(site_url or 'sitio').replace('https://','').replace('http://','').strip('/') } - Analisis Core Update - {datetime.now().date()}"
    out_title = st.text_input("Título del documento de salida", value=suggested_title)
with col7:
    dest_info = st.caption("Se copiará en el Drive de la service account. Compartí el template con ella.")

run = st.button("🚀 Ejecutar análisis y exportar al Template")

if run:
    if not site_url:
        st.error("Elegí un sitio primero.")
        st.stop()
    if start_dt > end_dt:
        st.error("La fecha de inicio no puede ser mayor que la de fin.")
        st.stop()
    if not tipos:
        st.error("Elegí al menos un tipo de tráfico.")
        st.stop()

    # 1) Copiar template
    with st.status("Copiando template de Google Sheets…", expanded=False) as status:
        try:
            new_sheet_id = copy_template_sheet(drive_service, template_id, out_title)
            sh_url = f"https://docs.google.com/spreadsheets/d/{new_sheet_id}"
            status.update(label="Template copiado ✅", state="complete")
        except HttpError as e:
            st.error(f"No pude copiar el template: {e}")
            st.stop()

    # 2) Traer datos y escribir
    results = {}
    if "Search" in tipos:
        with st.status("Consultando Search (web)…", expanded=False) as s:
            df_search = fetch_site_daily_totals(
                gsc_service, site_url, start_dt, end_dt, "Search", country, section
            )
            s.update(label=f"Search: {len(df_search)} filas", state="complete")
            results["Search | Datos Diarios"] = df_search
    if "Discover" in tipos:
        with st.status("Consultando Discover…", expanded=False) as s:
            df_disc = fetch_site_daily_totals(
                gsc_service, site_url, start_dt, end_dt, "Discover", country, section
            )
            s.update(label=f"Discover: {len(df_disc)} filas", state="complete")
            results["Discover | Datos Diarios"] = df_disc

    # 3) Escribir en el Sheet (preserva formato del template si ya existe la hoja)
    with st.status("Escribiendo datos en Google Sheets…", expanded=False) as s:
        try:
            for sheet_name, df in results.items():
                sh, ws = ensure_worksheet(gs_client, new_sheet_id, sheet_name)
                write_df_to_sheet(ws, df)
            s.update(label="Datos escritos ✅", state="complete")
        except Exception as e:
            st.error(f"No pude escribir en el Google Sheet: {e}")
            st.stop()

    st.success("¡Listo! Documento creado y cargado.")
    st.write("Abrilo acá:")
    st.link_button("📄 Abrir Google Sheet", sh_url)
