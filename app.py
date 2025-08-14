# app.py
import os
import sys
import streamlit as st

# --- Permitir http://localhost en el redirect de OAuth (útil en Streamlit Cloud) ---
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

# --- Config UI ---
st.set_page_config(layout="wide", page_title="Análisis SEO – GSC ➜ Google Sheets", page_icon="📊")
st.title("Análisis SEO – GSC ➜ Google Sheets")

# --- Asegurar imports del paquete local "modules/" ---
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# --- Imports de módulos propios ---
from modules.ui import (
    get_user, sidebar_user_info, login_screen,
    pick_site, pick_analysis, params_for_core_update, params_for_evergreen,
)
from modules.auth import (
    pick_destination_oauth, pick_source_oauth, get_cached_personal_creds,
)
from modules.drive import (
    ensure_drive_clients, get_google_identity, pick_destination, share_controls,
)
from modules.gsc import ensure_sc_client
from modules.analysis import run_core_update, run_evergreen
from modules.utils import token_store  # para "Cambiar cuenta PERSONAL"

# --- Debug switch (opcional) ---
st.session_state.setdefault("DEBUG", bool(st.secrets.get("debug", False)))

# === Login de Streamlit (app privada) ===
user = get_user()
if not user or not getattr(user, "is_logged_in", False):
    login_screen()
    st.stop()

sidebar_user_info(user)
st.checkbox("🔧 Modo debug (Drive/GSC)", key="DEBUG")

# ======================================================================================
# PASO 1: OAuth PERSONAL (Drive/Sheets) — se omite UI si ya hay token válido en caché
# ======================================================================================
creds_dest = get_cached_personal_creds()  # intenta store+session y refresca en silencio

if not creds_dest:
    # Solo si NO hay token válido mostramos la UI del Paso 1
    creds_dest = pick_destination_oauth()
    if not creds_dest:
        st.stop()

# Conectamos clientes de Drive/Sheets de la CUENTA PERSONAL
drive_service, gs_client = ensure_drive_clients(creds_dest)

# Identidad de Google conectada (dueña de los archivos)
_me = get_google_identity(drive_service)
if _me:
    st.caption(f"Google conectado como: **{_me.get('displayName','?')}** ({_me.get('emailAddress','?')})")
    st.info(f"✅ Los archivos se guardarán en el Drive de: {_me.get('emailAddress','?')}")
else:
    st.caption("No se pudo determinar el correo de la cuenta de Google conectada.")

# (Opcional) advertencia si email de Streamlit != Google conectado
_app_email = getattr(user, "email", None)
_google_email = (_me or {}).get("emailAddress")
STRICT_DEST = bool(st.secrets.get("strict_dest_email", False))  # modo estricto opcional
if _app_email and _google_email and _app_email.lower() != _google_email.lower():
    if STRICT_DEST:
        st.error(
            "Tu login de Streamlit es **%s**, pero Google conectado es **%s**. "
            "Activaste modo estricto, reautoriza con **%s** en el Paso 1."
            % (_app_email, _google_email, _app_email)
        )
        st.stop()
    else:
        st.warning(
            "Estás logueado en Streamlit como **%s**, pero Google conectado es **%s**. "
            "Los archivos se crearán en **%s**."
            % (_app_email, _google_email, _google_email)
        )

# (Opcional) botón para cambiar de cuenta PERSONAL aunque estemos conectados
with st.expander("Cuenta personal (Drive/Sheets)", expanded=False):
    st.write(f"Conectado como: `{_google_email or 'desconocido'}`")
    if st.button("Cambiar cuenta PERSONAL"):
        store = token_store()
        try:
            if _google_email:
                store["dest"].pop(_google_email, None)
            cache = st.session_state.setdefault("creds_cache", {"dest": {}, "src": {}})
            if _google_email:
                cache["dest"].pop(_google_email, None)
        except Exception:
            pass
        st.session_state.pop("creds_dest", None)
        st.session_state.pop("oauth_dest", None)
        st.experimental_rerun()

# Carpeta destino opcional (en la misma cuenta personal)
dest_folder_id = pick_destination(drive_service, _me)

# ==========================================================
# PASO 2: OAuth fuente (Search Console: ACCESO / ACCESO_MEDIOS)
# ==========================================================
creds_src = pick_source_oauth()
if not creds_src:
    st.stop()

sc_service = ensure_sc_client(creds_src)

# ============================================
# PASO 3: elegir sitio (de la cuenta de SC)
# ============================================
site_url = pick_site(sc_service)

# ============================================
# PASO 4: elegir análisis y ejecutar
# ============================================
analisis = pick_analysis()

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
