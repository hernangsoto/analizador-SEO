import os
import streamlit as st

# Permitir loopback http://localhost en OAuth (necesario en Streamlit Cloud)
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

st.set_page_config(layout="wide", page_title="Análisis SEO - Nomadic", page_icon="🚀")
st.title("Analizador SEO Nomadic")

from modules.ui import (
    get_user, sidebar_user_info, login_screen,
    pick_site, pick_analysis, params_for_core_update, params_for_evergreen,
)
from modules.auth import pick_destination_oauth, pick_source_oauth
from modules.drive import (
    ensure_drive_clients, get_google_identity, pick_destination,
    share_controls,
)
from modules.gsc import ensure_sc_client
from modules.analysis import run_core_update, run_evergreen

# Debug switch
st.session_state.setdefault("DEBUG", bool(st.secrets.get("debug", False)))

# --- Auth de Streamlit (primer login) ---
user = get_user()
if not user or not getattr(user, "is_logged_in", False):
    login_screen()
    st.stop()

sidebar_user_info(user)
st.checkbox("🔧 Modo debug (Drive/GSC)", key="DEBUG")

# --- Paso 1: OAuth personal (Drive/Sheets) ---
# app.py
from modules.auth import pick_destination_oauth, pick_source_oauth, get_cached_personal_creds
from modules.drive import ensure_drive_clients, get_google_identity, pick_destination

# --- Paso 1: OAuth personal (Drive/Sheets) ---
# 👉 Primero intentamos recuperar token automáticamente (sin UI)
creds_dest = get_cached_personal_creds()

if not creds_dest:
    # Solo si NO hay token válido mostramos la UI de autorización
    with st.container():
        creds_dest = pick_destination_oauth()
        if not creds_dest:
            st.stop()

# Ya tenemos credenciales personales válidas
drive_service, gs_client = ensure_drive_clients(creds_dest)

_me = get_google_identity(drive_service)
if _me:
    st.caption(f"Google conectado como: **{_me.get('displayName','?')}** ({_me.get('emailAddress','?')})")
    st.info(f"✅ Los archivos se guardarán en el Drive de: {_me.get('emailAddress','?')}")
else:
    st.caption("No se pudo determinar el correo de la cuenta de Google conectada.")

_app_email = getattr(user, "email", None)
_google_email = (_me or {}).get("emailAddress")
if _app_email and _google_email and _app_email.lower() != _google_email.lower():
    st.warning(
        "Estás logueado en Streamlit como **%s**, pero la cuenta de Google conectada es **%s**. "
        "Los archivos se crearán en **%s**. Si querés que se guarden en %s, reautorizá en Paso 1 con ese correo."
        % (_app_email, _google_email, _google_email, _app_email)
    )

dest_folder_id = pick_destination(drive_service, _me)


# --- Paso 2: OAuth fuente (Search Console: ACCESO/ACCESO_MEDIOS) ---
creds_src = pick_source_oauth()
if not creds_src:
    st.stop()

sc_service = ensure_sc_client(creds_src)

# --- Paso 3: elegir sitio y análisis ---
site_url = pick_site(sc_service)
analisis = pick_analysis()

# --- Paso 4: ejecutar ---
if analisis == "4":
    params = params_for_core_update()
    if st.button("🚀 Ejecutar análisis de Core Update", type="primary"):
        sid = run_core_update(sc_service, drive_service, gs_client, site_url, params, dest_folder_id)
        st.success("¡Listo! Tu documento está creado.")
        st.markdown(f"➡️ **Abrir Google Sheets**: https://docs.google.com/spreadsheets/d/{sid}")
        share_controls(drive_service, sid, default_email=(_me or {}).get("emailAddress"))
elif analisis == "5":
    params = params_for_evergreen()
    if st.button("🌲 Ejecutar análisis Evergreen", type="primary"):
        sid = run_evergreen(sc_service, drive_service, gs_client, site_url, params, dest_folder_id)
        st.success("¡Listo! Tu documento está creado.")
        st.markdown(f"➡️ **Abrir Google Sheets**: https://docs.google.com/spreadsheets/d/{sid}")
        share_controls(drive_service, sid, default_email=(_me or {}).get("emailAddress"))
else:
    st.info("Las opciones 1, 2 y 3 aún no están disponibles en esta versión.")
