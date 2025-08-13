import streamlit as st
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials
import gspread
from gspread_dataframe import set_with_dataframe
import pandas as pd
from datetime import date, datetime
import requests, urllib.parse
import json

"""
Analizador de Medios — Streamlit (SOLO OAuth de usuario)
-------------------------------------------------------
- Login con Google vía OAuth (no Service Account).
- Lee secrets anidados: [gcp_oauth_client] -> [gcp_oauth_client.web].
- Si faltan secrets, permite pegar client_id / client_secret / redirect_uri en la UI.
- Tras login: seleccionás propiedad, seteás parámetros y exportás a un Google Sheet
  copiado desde tu Template.

Secrets (Streamlit Cloud → Settings → Secrets):
[gcp_oauth_client]
  [gcp_oauth_client.web]
  client_id = "8289...apps.googleusercontent.com"
  client_secret = "GOCSPX-..."
  auth_uri = "https://accounts.google.com/o/oauth2/auth"
  token_uri = "https://oauth2.googleapis.com/token"
  redirect_uris = ["https://hernangsoto.streamlit.app/"]
"""

SCOPES = [
    "https://www.googleapis.com/auth/webmasters.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

st.set_page_config(page_title="Analizador de Medios (OAuth)", page_icon="📈", layout="wide")
st.title("📈 Analizador de Medios — OAuth (usuario)")
st.caption("Iniciá sesión con tu cuenta (que tenga acceso al sitio en Search Console) y exportá datos diarios a un Google Sheet basado en tu template.")

# -------------------------------------------------------------
# OAuth config: secrets primero, UI fallback
# -------------------------------------------------------------

def load_oauth_cfg():
    cfg = None
    try:
        cfg = dict(st.secrets["gcp_oauth_client"]["web"])  # TOML anidado correcto
    except Exception:
        pass
    if cfg:
        return cfg
    st.warning("No encontré gcp_oauth_client → web en Secrets. Pegá los datos abajo para probar.")
    c1, c2 = st.columns(2)
    with c1:
        cid = st.text_input("client_id")
        csecret = st.text_input("client_secret", type="password")
    with c2:
        redirect_uri = st.text_input("redirect_uri", value="https://hernangsoto.streamlit.app/")
    if cid and csecret and redirect_uri:
        return {
            "client_id": cid,
            "client_secret": csecret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    st.stop()


def build_flow(client_config: dict, redirect_uri: str):
    ccfg = client_config if "web" in client_config else {"web": client_config}
    flow = Flow.from_client_config(ccfg, scopes=SCOPES)
    flow.redirect_uri = redirect_uri
    return flow


def start_login(oauth_cfg: dict):
    redirect_uri = oauth_cfg["redirect_uris"][0]
    flow = build_flow(oauth_cfg, redirect_uri)
    auth_url, state = flow.authorization_url(
        access_type="offline", include_granted_scopes="true", prompt="consent",
    )
    st.session_state["_oauth_cfg"] = oauth_cfg
    st.session_state["_redirect_uri"] = redirect_uri
    st.session_state["_oauth_state"] = state
    st.session_state["_auth_url"] = auth_url


def handle_callback():
    qp = {}
    try:
        qp = dict(st.query_params)
    except Exception:
        pass
    if qp.get("code") and st.session_state.get("_oauth_cfg"):
        code = qp["code"][0] if isinstance(qp.get("code"), list) else qp.get("code")
        state_back = qp.get("state")
        if isinstance(state_back, list):
            state_back = state_back[0]
        if st.session_state.get("_oauth_state") and state_back and state_back != st.session_state.get("_oauth_state"):
            st.error("Estado OAuth inválido. Reintentá el login.")
            st.stop()
        flow = build_flow(st.session_state["_oauth_cfg"], st.session_state["_redirect_uri"])
        flow.fetch_token(code=code)
        st.session_state["oauth_creds"] = flow.credentials
        st.success("✅ Login correcto")


# -------------------------------------------------------------
# Helpers de GSC / Drive / Sheets
# -------------------------------------------------------------

def list_verified_sites(creds) -> list:
    try:
        svc = build("webmasters", "v3", credentials=creds)
        resp = svc.sites().list().execute() or {}
        entries = resp.get("siteEntry", [])
        sites = [e["siteUrl"] for e in entries if e.get("permissionLevel") != "siteUnverifiedUser"]
        return sorted(sites)
    except Exception as e:
        st.error(f"No pude listar sitios: {e}")
        return []


def _fetch_all_rows(gsc_service, site_url: str, body: dict, page_size: int = 25000):
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


def fetch_site_daily_totals(creds, site_url: str, start_dt: date, end_dt: date,
                             tipo: str, country_iso3: str | None, section_path: str | None) -> pd.DataFrame:
    gsc = build("webmasters", "v3", credentials=creds)
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
        frag = section_path.strip("/")
        filters.append({"dimension": "page", "operator": "contains", "expression": f"/{frag}"})
    if filters:
        body["dimensionFilterGroups"] = [{"filters": filters}]

    rows = _fetch_all_rows(gsc, site_url, body, page_size=5000)
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


def copy_template_sheet(creds, template_id: str, new_title: str) -> str:
    drive = build("drive", "v3", credentials=creds)
    body = {"name": new_title}
    new_file = drive.files().copy(fileId=template_id, body=body).execute()
    return new_file["id"]


def ensure_worksheet(creds, spreadsheet_key: str, sheet_title: str):
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(spreadsheet_key)
    try:
        ws = sh.worksheet(sheet_title)
        ws.clear()
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=sheet_title, rows=2000, cols=20)
    return sh, ws


def write_df_to_sheet(ws, df: pd.DataFrame):
    out = df.copy()
    if "Fecha" in out.columns:
        out["Fecha"] = pd.to_datetime(out["Fecha"]).dt.date.astype(str)
    set_with_dataframe(ws, out, include_index=False, include_column_header=True, resize=True)


# -------------------------------------------------------------
# UI: Login y parámetros de análisis
# -------------------------------------------------------------

oauth_cfg = load_oauth_cfg()

if not st.session_state.get("oauth_creds"):
    st.info("Iniciá sesión con tu cuenta de Google (debe tener acceso en Search Console).")
    colA, colB = st.columns([1, 1])
    with colA:
        if st.button("🔐 Iniciar sesión con Google"):
            start_login(oauth_cfg)
    with colB:
        if st.session_state.get("_auth_url"):
            st.link_button("Abrir Google Login", st.session_state["_auth_url"])  
    handle_callback()
    st.stop()

creds = st.session_state["oauth_creds"]
with st.sidebar:
    if st.button("Cerrar sesión"):
        st.session_state["oauth_creds"] = None
        st.rerun()

# Sitios disponibles
sites = list_verified_sites(creds)
colA, colB = st.columns([2, 1])
with colA:
    site_url = st.selectbox("Sitio verificado en Search Console", sites, placeholder="Elegí un sitio")
with colB:
    st.link_button("Abrir Search Console", "https://search.google.com/search-console", disabled=not bool(site_url))

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
TEMPLATE_ID_DEFAULT = "1dTGsOyJHWZlvONQa_9WuiaXBDLPCif4A7hE0gSPZcBU"  # tu template por defecto
col6, col7 = st.columns([2, 1])
with col6:
    template_id = st.text_input("ID del Template de Sheets", value=TEMPLATE_ID_DEFAULT)
    suggested_title = f"{(site_url or 'sitio').replace('https://','').replace('http://','').strip('/') } - Analisis Core Update - {datetime.now().date()}"
    out_title = st.text_input("Título del documento de salida", value=suggested_title)
with col7:
    st.caption("Se copiará en tu Drive (tu cuenta). Compartí el template contigo mismo si no sos owner.")

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

    # 1) Copiar template a tu Drive
    with st.status("Copiando template de Google Sheets…", expanded=False) as status:
        try:
            new_sheet_id = copy_template_sheet(creds, template_id, out_title)
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
                creds, site_url, start_dt, end_dt, "Search", country, section
            )
            s.update(label=f"Search: {len(df_search)} filas", state="complete")
            results["Search | Datos Diarios"] = df_search
    if "Discover" in tipos:
        with st.status("Consultando Discover…", expanded=False) as s:
            df_disc = fetch_site_daily_totals(
                creds, site_url, start_dt, end_dt, "Discover", country, section
            )
            s.update(label=f"Discover: {len(df_disc)} filas", state="complete")
            results["Discover | Datos Diarios"] = df_disc

    # 3) Escribir datos a Sheets
    with st.status("Escribiendo datos en Google Sheets…", expanded=False) as s:
        try:
            for sheet_name, df in results.items():
                sh, ws = ensure_worksheet(creds, new_sheet_id, sheet_name)
                write_df_to_sheet(ws, df)
            s.update(label="Datos escritos ✅", state="complete")
        except Exception as e:
            st.error(f"No pude escribir en el Google Sheet: {e}")
            st.stop()

    st.success("¡Listo! Documento creado y cargado.")
    st.link_button("📄 Abrir Google Sheet", sh_url)
