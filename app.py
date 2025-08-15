# app.py
from __future__ import annotations

import os
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

from datetime import date, timedelta
import pandas as pd
import streamlit as st
from google.oauth2.credentials import Credentials  # ← para rehidratar desde session_state

# ============== Config base ==============
st.set_page_config(layout="wide", page_title="Análisis SEO", page_icon="📊")

# ====== UI / Branding ======
from modules.ui import (
    apply_page_style,
    render_brand_header_once,
    enable_brand_auto_align,
    get_user,
    sidebar_user_info,
    login_screen,
)

# Colores/posicionamiento del header + logo
HEADER_COLOR = "#5c417c"
HEADER_HEIGHT = 64
LOGO_URL = "https://nomadic.agency/wp-content/uploads/2021/03/logo-blanco.png"

apply_page_style(
    header_bg=HEADER_COLOR,
    header_height_px=HEADER_HEIGHT,
    page_bg="#ffffff",
    use_gradient=False,
    band_height_px=110,
)

render_brand_header_once(
    LOGO_URL,
    height_px=27,
    pinned=True,
    nudge_px=-42,     # negativo = subir
    x_align="left",
    x_offset_px=40,   # mover a la derecha
    z_index=3000,
    container_max_px=1200,
)
enable_brand_auto_align()

st.title("Analizador SEO 🚀")

# ====== Utils / paquete externo ======
from modules.utils import debug_log, ensure_external_package

_ext = ensure_external_package()
USING_EXT = bool(_ext and hasattr(_ext, "run_core_update") and hasattr(_ext, "run_evergreen"))
if USING_EXT:
    run_core_update = _ext.run_core_update
    run_evergreen = _ext.run_evergreen
else:
    from modules.analysis import run_core_update, run_evergreen  # type: ignore

# ====== OAuth / Clientes ======
from modules.auth import pick_destination_oauth, pick_source_oauth
from modules.drive import (
    ensure_drive_clients,
    get_google_identity,
    pick_destination,
    share_controls,
)
from modules.gsc import ensure_sc_client


# ====== Pequeñas utilidades UI (parámetros y selección) ======
def pick_site(sc_service):
    st.subheader("2) Elegí el sitio a trabajar (Search Console)")

    # Traer y normalizar la lista (orden estable)
    try:
        site_list = sc_service.sites().list().execute()
        sites = site_list.get("siteEntry", [])
    except Exception as e:
        st.error(f"Error al obtener sitios: {e}")
        st.stop()

    verified = [s for s in sites if s.get("permissionLevel") != "siteUnverifiedUser"]
    if not verified:
        st.error("No se encontraron sitios verificados en esta cuenta.")
        st.stop()

    verified_urls = sorted({s["siteUrl"] for s in verified})  # orden alfabético + sin duplicados

    # Persistir opciones/selección
    prev_options = st.session_state.get("site_options") or []
    if prev_options != verified_urls:
        st.session_state["site_options"] = verified_urls
        if st.session_state.get("site_selected") not in verified_urls:
            st.session_state["site_selected"] = verified_urls[0]

    options = st.session_state["site_options"]
    current = st.session_state.get("site_selected", options[0])

    try:
        idx = options.index(current)
    except ValueError:
        idx = 0

    choice = st.selectbox(
        "Sitio verificado:",
        options,
        index=idx,
        key="site_select_widget",
    )
    st.session_state["site_selected"] = choice
    return choice


def pick_analysis():
    st.subheader("3) Elegí el tipo de análisis")
    opciones = {
        "1. Análisis de entidades (🚧 próximamente)": "1",
        "2. Análisis de tráfico general (🚧 próximamente)": "2",
        "3. Análisis de secciones (🚧 próximamente)": "3",
        "4. Análisis de impacto de Core Update ✅": "4",
        "5. Análisis de tráfico evergreen ✅": "5",
    }
    key = st.radio("Tipos disponibles:", list(opciones.keys()), index=3, key="analysis_choice")
    return opciones[key]


LAG_DAYS_DEFAULT = 3

def params_for_core_update():
    st.markdown("#### Parámetros (Core Update)")
    lag_days = st.number_input("Lag de datos (para evitar días incompletos)", 0, 7, LAG_DAYS_DEFAULT, key="lag_core")
    fecha_inicio = st.date_input("¿Cuándo inició el Core Update? (YYYY-MM-DD)", key="core_ini")
    termino = st.radio("¿El Core Update ya terminó?", ["sí", "no"], horizontal=True, key="core_end")
    fecha_fin = None
    if termino == "sí":
        fecha_fin = st.date_input("¿Cuándo finalizó el Core Update? (YYYY-MM-DD)", key="core_fin")
    tipo = st.selectbox("Datos a analizar", ["Search", "Discover", "Ambos"], index=2, key="tipo_core")
    pais_choice = st.selectbox(
        "¿Filtrar por país? (ISO-3)",
        ["Todos", "ARG", "MEX", "ESP", "USA", "COL", "PER", "CHL", "URY"],
        index=0,
        key="pais_core",
    )
    pais = None if pais_choice == "Todos" else pais_choice
    seccion = st.text_input("¿Limitar a una sección? (path, ej: /vida/)", value="", key="sec_core") or None
    return lag_days, fecha_inicio, termino, fecha_fin, tipo, pais, seccion


def params_for_evergreen():
    st.markdown("#### Parámetros (Evergreen)")
    st.caption("Se usa el período más amplio posible de **meses completos** (hasta 16) en Search.")
    lag_days = st.number_input("Lag de datos (para evitar días incompletos)", 0, 7, LAG_DAYS_DEFAULT, key="lag_ev")
    pais_choice = st.selectbox(
        "¿Filtrar por país? (ISO-3)",
        ["Todos", "ARG", "MEX", "ESP", "USA", "COL", "PER", "CHL", "URY"],
        index=0,
        key="pais_ev",
    )
    pais = None if pais_choice == "Todos" else pais_choice
    seccion = st.text_input("¿Limitar a una sección? (path, ej: /vida/)", value="", key="sec_ev") or None

    # Ventana de 16 meses completos
    hoy_util = date.today() - timedelta(days=lag_days)
    end_month_first_day = (pd.Timestamp(hoy_util.replace(day=1)) - pd.offsets.MonthBegin(1))
    end_month_last_day = (end_month_first_day + pd.offsets.MonthEnd(0))
    start_month_first_day = (end_month_last_day - pd.DateOffset(months=15)).replace(day=1)
    start_date = start_month_first_day.date()
    end_date = end_month_last_day.date()
    st.info(f"Ventana mensual: {start_date} → {end_date}")

    incluir_diario = st.checkbox("Incluir análisis diario por URL (lento)", value=False, key="daily_ev")
    return lag_days, pais, seccion, incluir_diario, start_date, end_date


# ============== App ==============
user = get_user()
if not user or not getattr(user, "is_logged_in", False):
    login_screen()
    st.stop()

# 👉 Callback para inyectar UI extra dentro de “🧹 Mantenimiento” (sidebar)
def maintenance_extra_ui():
    if USING_EXT:
        st.caption("🧩 Usando análisis del paquete externo (repo privado).")
    else:
        st.caption("🧩 Usando análisis embebidos en este repo.")
    st.checkbox("🔧 Modo debug (Drive/GSC)", key="DEBUG")

# Sidebar info (con “Mantenimiento” extendido)
sidebar_user_info(user, maintenance_extra=maintenance_extra_ui)

# --- Paso 1: OAuth PERSONAL (Drive/Sheets) ---
st.subheader("1) Conectar Google PERSONAL (Drive/Sheets)")

creds_dest = None
drive_service = None
gs_client = None
_me = None

# Si YA está autenticado, ocultamos el flujo y mostramos resumen + botón "Cambiar"
if st.session_state.get("creds_dest"):
    creds_dest = Credentials(**st.session_state["creds_dest"])
    drive_service, gs_client = ensure_drive_clients(creds_dest)
    _me = get_google_identity(drive_service)
    email_txt = (_me or {}).get("emailAddress", "?")
    st.success(f"Los archivos se guardarán en el Drive de: **{email_txt}**")
    if st.button("Cambiar mail personal", key="btn_change_personal"):
        # Limpia todo lo relacionado al Paso 1
        st.session_state.pop("oauth_dest", None)
        st.session_state.pop("creds_dest", None)
        st.session_state.pop("dest_folder_id", None)
        st.rerun()
else:
    # Mostrar el flujo normal de OAuth si no hay credenciales
    creds_dest = pick_destination_oauth()
    if not creds_dest:
        st.stop()
    drive_service, gs_client = ensure_drive_clients(creds_dest)
    _me = get_google_identity(drive_service)
    email_txt = (_me or {}).get("emailAddress", "?")
    st.success(f"Los archivos se guardarán en el Drive de: **{email_txt}**")

# Carpeta destino opcional (se mantiene igual)
dest_folder_id = pick_destination(drive_service, _me)

# --- Paso 2: Conectar Search Console (fuente) ---
creds_src = pick_source_oauth()
if not creds_src:
    st.stop()
sc_service = ensure_sc_client(creds_src)

# --- Paso 3: sitio + análisis ---
site_url = pick_site(sc_service)
analisis = pick_analysis()

# --- Paso 4: ejecutar ---
if analisis == "4":
    params = params_for_core_update()
    if st.button("🚀 Ejecutar análisis de Core Update", type="primary"):
        sid = run_core_update(sc_service, drive_service, gs_client, site_url, params, dest_folder_id)
        st.success("¡Listo! Tu documento está creado.")
        st.markdown(f"➡️ **Abrir Google Sheets**: https://docs.google.com/spreadsheets/d/{sid}")
        st.session_state["last_file_id"] = sid
        share_controls(drive_service, sid, default_email=_me.get("emailAddress") if _me else None)

elif analisis == "5":
    params = params_for_evergreen()
    if st.button("🌲 Ejecutar análisis Evergreen", type="primary"):
        sid = run_evergreen(sc_service, drive_service, gs_client, site_url, params, dest_folder_id)
        st.success("¡Listo! Tu documento está creado.")
        st.markdown(f"➡️ **Abrir Google Sheets**: https://docs.google.com/spreadsheets/d/{sid}")
        st.session_state["last_file_id"] = sid
        share_controls(drive_service, sid, default_email=_me.get("emailAddress") if _me else None)
else:
    st.info("Las opciones 1, 2 y 3 aún no están disponibles en esta versión.")
