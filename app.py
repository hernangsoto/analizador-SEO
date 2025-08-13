import streamlit as st
import pandas as pd
from google.oauth2 import service_account
from googleapiclient.discovery import build

# --- Auth (Service Account) ---
SCOPES = [
    "https://www.googleapis.com/auth/webmasters.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
creds = service_account.Credentials.from_service_account_info(
    st.secrets["gcp_service_account"], scopes=SCOPES
)
gsc_service = build("searchconsole", "v1", credentials=creds)
drive_service = build("drive", "v3", credentials=creds)

st.title("Analizador de Medios (Streamlit)")

# 1) elegir sitio
sites = gsc_service.sites().list().execute().get("siteEntry", [])
sites = [s for s in sites if s.get("permissionLevel") != "siteUnverifiedUser"]
site_urls = [s["siteUrl"] for s in sites]
site_url = st.selectbox("Sitio verificado", site_urls)

# 2) elegir análisis
tipo = st.radio("Tipo de análisis", ["Core Update", "Evergreen"])
col1, col2 = st.columns(2)

if tipo == "Core Update":
    fecha_inicio = col1.date_input("Inicio Core Update")
    termino = col2.toggle("¿Finalizado?")
    pais = st.text_input("País (ISO-3, opcional)", "").strip().upper() or None
    seccion = st.text_input("Sección (ej: /vida/ , opcional)", "").strip() or None

    if st.button("Ejecutar análisis Core Update"):
        with st.status("Consultando GSC…", expanded=False):
            # TODO: llama a tus funciones consultar_datos(), etc.
            st.write("Ejecutando… (integra aquí tus helpers)")

else:
    pais = st.text_input("País (ISO-3, opcional)", "").strip().upper() or None
    seccion = st.text_input("Sección (ej: /vida/ , opcional)", "").strip() or None
    incluir_diario = st.checkbox("Incluir análisis diario (lento)")
    if st.button("Ejecutar análisis Evergreen"):
        with st.status("Consultando GSC…", expanded=False):
            # TODO: llama a tus funciones evergreen (mensual/diario)
            st.write("Ejecutando… (integra aquí tus helpers)")

st.caption("Tip: compartí el template de Sheets y el sitio de GSC con la service account.")
