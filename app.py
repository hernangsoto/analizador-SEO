# app.py
from __future__ import annotations

import os
from datetime import date, timedelta

import streamlit as st
import pandas as pd

# ============== Config base ==============
st.set_page_config(layout="wide", page_title="Análisis SEO", page_icon="📊")

# ====== UI / Branding ======
from modules.ui import (
    apply_page_style,
    render_brand_header_once,
    hide_old_logo_instances,
    get_user,
    sidebar_user_info,
    login_screen,
)

# Colores/posicionamiento del header + logo
HEADER_COLOR = "#5c417c"
HEADER_HEIGHT = 64  # ajustá si tu header se ve un poco más alto
LOGO_URL = "https://nomadic.agency/wp-content/uploads/2021/03/logo-blanco.png"

apply_page_style(
    header_bg=HEADER_COLOR,
    header_height_px=HEADER_HEIGHT,
    page_bg="#ffffff",
    use_gradient=False,
    band_height_px=110,
)

# Si querés forzar rerender durante pruebas, descomentá:
# st.session_state.pop("_brand_sig", None)

# Logo anclado (fixed), sin recuadro ni sombra
render_brand_header_once(
    LOGO_URL,
    height_px=27,
    pinned=True,          # anclado
    nudge_px=-8,          # negativo = subir; positivo = bajar
    x_align="left",       # "left" | "center" | "right"
    x_offset_px=140,      # mover a la derecha (si x_align="left")
    z_index=3000,         # por delante del header
    container_max_px=1200 # alineado con el contenido
)
# Ocultar logos por defecto del tema (opcional)
# hide_old_logo_instances()

st.title("Análisis SEO – GSC ➜ Google Sheets")


# ====== Utils / paquete externo ======
from modules.utils import debug_log, ensure_external_package

_ext = ensure_external_package()
if _ext and hasattr(_ext, "run_core_update") and hasattr(_ext, "run_evergreen"):
    run_core_update = _ext.run_core_update
    run_evergreen = _ext.run_evergreen
    st.caption("🧩 Usando análisis del paquete externo (repo privado).")
else:
    from modules.analysis import run_core_update, run_evergreen  # type: ignore
    st.caption("🧩 Usando análisis embebidos en este repo.")


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
    site_url = st.selectbox("Sitio verificado:", [s["siteUrl"] for s in verified], key="site_select")
    return site_url


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
    start_month_first_day = (end_month_first_day - pd.DateOffset(months=15))
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

# Sidebar info
sidebar_user_info(user)

# Debug switch (opcional)
st.checkbox("🔧 Modo debug (Drive/GSC)", key="DEBUG")

# --- Paso 1: OAuth personal (Drive/Sheets) ---
creds_dest = pick_destination_oauth()
if not creds_dest:
    st.stop()

drive_service, gs_client = ensure_drive_clients(creds_dest)
_me = get_google_identity(drive_service)
if _me:
    st.success(f"Los archivos se guardarán en el Drive de: **{_me.get('emailAddress','?')}**")
else:
    st.caption("No se pudo determinar el correo de la cuenta de Google conectada.")

# Carpeta destino opcional
dest_folder_id = pick_destination(drive_service, _me)

# --- Paso 2: Conectar Search Console (fuente de datos) ---
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
