# app.py
from __future__ import annotations

import os
import streamlit as st

# Permite http://localhost en el authorization_response y tolera scopes reordenados
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

# ====== Módulos propios ======
from modules.ui import (
    get_user,
    login_screen,
    sidebar_user_info,
    pick_site,
    pick_analysis,
    params_for_core_update,
    params_for_evergreen,
)
from modules.auth import (
    pick_destination_oauth,   # OAuth personal (Drive/Sheets)
    pick_source_oauth,        # OAuth fuente (Search Console)
)
from modules.drive import (
    ensure_drive_clients,
    get_google_identity,
    pick_destination,
    share_controls,
)
from modules.gsc import ensure_sc_client
from modules.utils import debug_log

# Analíticas separadas por archivo
from modules.analysis_core_update import run_core_update
from modules.analysis_evergreen import run_evergreen

# (Opcional) estilos
try:
    from modules.style import inject as inject_styles
except Exception:
    inject_styles = None


# =============================
# Config
# =============================
DEBUG_DEFAULT = bool(st.secrets.get("debug", False))

st.set_page_config(
    layout="wide",
    page_title="Análisis SEO",
    page_icon="📊",
)

if inject_styles:
    inject_styles()

st.title("Análisis SEO – GSC → Google Sheets")
st.session_state.setdefault("DEBUG", DEBUG_DEFAULT)


# =============================
# Autenticación de la app
# =============================
user = get_user()
if not user or not getattr(user, "is_logged_in", False):
    login_screen()
    st.stop()

sidebar_user_info(user)

# Toggle de debug
st.checkbox("🔧 Modo debug (Drive/GSC)", key="DEBUG")


# =============================
# Paso 1: OAuth PERSONAL (Drive/Sheets)
# =============================
creds_dest = pick_destination_oauth()
if not creds_dest:
    st.stop()

# Clientes de Drive/Sheets usando la cuenta personal
drive_service, gs_client = ensure_drive_clients(creds_dest)

# Identidad Google conectada
_me = get_google_identity(drive_service)
if _me:
    st.success(f"Los archivos se guardarán en el Drive de: **{_me.get('emailAddress','?')}**")
else:
    st.caption("No se pudo determinar el correo de la cuenta de Google conectada.")

# Aviso si el email de Streamlit y el de Google difieren
_app_email = getattr(user, "email", None)
_google_email = (_me or {}).get("emailAddress")
if _app_email and _google_email and _app_email.lower() != _google_email.lower():
    st.warning(
        "Estás logueado en Streamlit como **%s**, pero la cuenta de Google conectada es **%s**. "
        "Los archivos se crearán en **%s**. Si querés que se guarden en **%s**, reautorizá el Paso 1 con ese correo."
        % (_app_email, _google_email, _google_email, _app_email)
    )

# Carpeta destino (opcional)
dest_folder_id = pick_destination(drive_service, _me)


# =============================
# Paso 2: OAuth FUENTE (Search Console)
# =============================
creds_src = pick_source_oauth()
if not creds_src:
    st.stop()

# Cliente de Search Console (fuente)
sc_service = ensure_sc_client(creds_src)


# =============================
# Paso 3: elegir sitio + análisis
# =============================
site_url = pick_site(sc_service)
analisis = pick_analysis()

# =============================
# Paso 4: parámetros + ejecución
# =============================
if analisis == "4":
    params = params_for_core_update()
    if st.button("🚀 Ejecutar análisis de Core Update", type="primary"):
        sid = run_core_update(sc_service, drive_service, gs_client, site_url, params, dest_folder_id)
        st.success("¡Listo! Tu documento está creado.")
        st.markdown(f"➡️ **Abrir Google Sheets**: https://docs.google.com/spreadsheets/d/{sid}")
        st.session_state["last_file_id"] = sid
        me_email = (_me or {}).get("emailAddress")
        share_controls(drive_service, sid, default_email=me_email)

elif analisis == "5":
    params = params_for_evergreen()
    if st.button("🌲 Ejecutar análisis Evergreen", type="primary"):
        sid = run_evergreen(sc_service, drive_service, gs_client, site_url, params, dest_folder_id)
        st.success("¡Listo! Tu documento está creado.")
        st.markdown(f"➡️ **Abrir Google Sheets**: https://docs.google.com/spreadsheets/d/{sid}")
        st.session_state["last_file_id"] = sid
        me_email = (_me or {}).get("emailAddress")
        share_controls(drive_service, sid, default_email=me_email)

else:
    st.info("Las opciones 1, 2 y 3 aún no están disponibles en esta versión.")
