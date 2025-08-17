# app.py
from __future__ import annotations

# --- Permisos OAuth en localhost + tolerancia de scope (útil para Streamlit Cloud + localhost redirect)
import os
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

from datetime import date, timedelta
import pandas as pd
import streamlit as st
from google.oauth2.credentials import Credentials

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

HEADER_COLOR = "#5c417c"
HEADER_HEIGHT = 64
LOGO_URL = "https://nomadic.agency/wp-content/uploads/2021/03/logo-blanco.png"

# Estilo general + header nativo
apply_page_style(
    header_bg=HEADER_COLOR,
    header_height_px=HEADER_HEIGHT,
    page_bg="#ffffff",
    use_gradient=False,
    band_height_px=110,
)

# Logo anclado (fixed), sin recuadro ni sombra, con offsets finos
render_brand_header_once(
    LOGO_URL,
    height_px=27,
    pinned=True,         # anclado
    nudge_px=-42,        # vertical fino: negativo = subir; positivo = bajar
    x_align="left",      # "left" | "center" | "right"
    x_offset_px=40,      # mover a la derecha si x_align="left"
    z_index=3000,        # por delante del header nativo
    container_max_px=1200,
)
# Autoalineación con el contenedor (responde a abrir/cerrar sidebar)
enable_brand_auto_align()

# ====== Estilos globales ======
st.markdown("""
<style>
/* Botones morado #8e7cc3 (para los de acción principal) */
.stButton > button, .stDownloadButton > button {
  background: #8e7cc3 !important;
  border-color: #8e7cc3 !important;
  color: #fff !important;
  border-radius: 8px !important;
}
.stButton > button:hover, .stDownloadButton > button:hover {
  filter: brightness(0.93);
}

/* Caja verde tipo "success" para resúmenes inline */
.success-inline {
  background: #e6f4ea;              /* verde claro */
  border: 1px solid #a5d6a7;        /* borde verde */
  color: #1e4620;                   /* texto verde oscuro */
  padding: 10px 14px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: .5rem;
}
.success-inline a {
  color: #0b8043;                   /* link verde */
  text-decoration: underline;
  font-weight: 600;
}
.success-inline strong { margin-left: .25rem; }

/* Asegurar que el header nativo no tape nuestro logo */
header[data-testid="stHeader"] { z-index: 1500 !important; }
</style>
""", unsafe_allow_html=True)

st.title("Analizador SEO 🚀")

# ====== Utils / paquete externo ======
from modules.utils import debug_log, ensure_external_package

USING_EXT = False
HAVE_AUDITORIA = False
run_core_update = None
run_evergreen = None
run_traffic_audit = None

_ext = ensure_external_package()
if _ext:
    USING_EXT = True
    if hasattr(_ext, "run_core_update"):
        run_core_update = _ext.run_core_update
    if hasattr(_ext, "run_evergreen"):
        run_evergreen = _ext.run_evergreen
    # Auditoría: aceptar run_traffic_audit o alias run_auditoria
    if hasattr(_ext, "run_traffic_audit") or hasattr(_ext, "run_auditoria"):
        HAVE_AUDITORIA = True
        run_traffic_audit = getattr(_ext, "run_traffic_audit", getattr(_ext, "run_auditoria"))
else:
    # Fallback local
    from modules.analysis import run_core_update, run_evergreen  # type: ignore
    try:
        from modules.analysis import run_traffic_audit as _local_run_traffic_audit  # type: ignore
        HAVE_AUDITORIA = True
        run_traffic_audit = _local_run_traffic_audit
    except Exception:
        HAVE_AUDITORIA = False

# ====== OAuth / Clientes ======
from modules.auth import pick_destination_oauth, pick_source_oauth
from modules.drive import (
    ensure_drive_clients,
    get_google_identity,
    pick_destination,     # UI para elegir carpeta (opcional)
    share_controls,
)
from modules.gsc import ensure_sc_client


# ====== Pequeñas utilidades UI (parámetros y selección) ======
def pick_site(sc_service):
    st.subheader("4) Elegí el sitio a trabajar (Search Console)")
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

    options = sorted({s["siteUrl"] for s in verified})
    prev = st.session_state.get("site_url_choice")
    index = options.index(prev) if prev in options else 0
    site_url = st.selectbox("Sitio verificado:", options, index=index, key="site_url_choice")
    return site_url


def pick_analysis():
    st.subheader("5) Elegí el tipo de análisis")
    options = [
        "1. Análisis de entidades (🚧 próximamente)",
        "2. Análisis de tráfico general (🚧 próximamente)",
        "3. Análisis de secciones (🚧 próximamente)",
        "4. Análisis de impacto de Core Update ✅",
        "5. Análisis de tráfico evergreen ✅",
    ]
    # Mostrar SIEMPRE la auditoría; marcar si requiere paquete externo
    if HAVE_AUDITORIA:
        options.append("6. Auditoría de tráfico ✅")
    else:
        options.append("6. Auditoría de tráfico (requiere paquete externo)")
    choice = st.radio("Tipos disponibles:", options, index=3, key="analysis_choice")
    return choice.split(".")[0]  # "4" | "5" | "6"


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


def params_for_auditoria():
    st.markdown("#### Parámetros (Auditoría de tráfico)")
    st.caption(
        "Un **período** es la unidad de tiempo de la auditoría. "
        "Se toma el período **actual** (respetando el *lag* para evitar días incompletos) "
        "y **N períodos previos** para comparar."
    )
    modo = st.selectbox("Modo de período", ["Semanal", "Quincenal", "Mensual", "Personalizado"], key="aud_modo")
    lag_days = st.number_input("Lag de datos (para evitar días incompletos)", 0, 7, 3, key="aud_lag")
    periods_back = st.number_input("¿Cuántos períodos previos querés comparar?", 1, 12, 4, key="aud_prev")
    custom_days = None
    if modo == "Personalizado":
        custom_days = st.number_input("Cantidad de días del período personalizado", 2, 90, 7, key="aud_days")

    tipo = st.selectbox("Origen", ["Search", "Discover", "Search y Discover"], key="aud_tipo")
    seccion = st.text_input("¿Sección? (vacío = todo el sitio)", value="", key="aud_sec") or None
    alcance = st.selectbox("Ámbito", ["Global", "País"], key="aud_alc")
    country = None
    if alcance == "País":
        country = st.text_input("ISO-3 del país (ej. ARG, ESP, USA)", value="ARG", key="aud_country").upper().strip()

    # orden de params como espera el runner externo
    return (modo, tipo, seccion, alcance, country, lag_days, custom_days, periods_back)


# ============== Helpers de query params para links inline ==============
def _get_qp() -> dict:
    try:
        return dict(st.query_params)
    except Exception:
        return st.experimental_get_query_params()  # fallback viejo

def _clear_qp():
    try:
        st.query_params.clear()
    except Exception:
        st.experimental_set_query_params()


# ============== App ==============
user = get_user()
if not user or not getattr(user, "is_logged_in", False):
    login_screen()
    st.stop()

# Sidebar → Mantenimiento: mensaje del paquete y modo debug
def maintenance_extra_ui():
    if USING_EXT:
        st.caption("🧩 Usando análisis del paquete externo (repo privado).")
    else:
        st.caption("🧩 Usando análisis embebidos en este repo.")
    st.checkbox("🔧 Modo debug (Drive/GSC)", key="DEBUG")

sidebar_user_info(user, maintenance_extra=maintenance_extra_ui)

# Estados de pasos
st.session_state.setdefault("step1_done", False)
st.session_state.setdefault("step2_done", False)
st.session_state.setdefault("step3_done", False)   # Search Console

# === Procesar acciones de links inline (antes de pintar los resúmenes) ===
_qp = _get_qp()
_action = _qp.get("action")
if isinstance(_action, list):
    _action = _action[0] if _action else None

if _action == "change_personal":
    for k in ("creds_dest", "oauth_dest", "step1_done"):
        st.session_state.pop(k, None)
    st.session_state["step2_done"] = False
    st.session_state.pop("dest_folder_id", None)
    _clear_qp()
    st.rerun()

elif _action == "change_folder":
    st.session_state["step2_done"] = False
    _clear_qp()
    st.rerun()

elif _action == "change_src":
    for k in ("creds_src", "oauth_src", "step3_done"):
        st.session_state.pop(k, None)
    _clear_qp()
    st.rerun()


# --- PASO 1: OAuth PERSONAL (Drive/Sheets) ---
creds_dest = None
if not st.session_state["step1_done"]:
    creds_dest = pick_destination_oauth()
    if not creds_dest:
        st.stop()
    st.session_state["step1_done"] = True
    st.session_state["creds_dest"] = {
        "token": creds_dest.token,
        "refresh_token": getattr(creds_dest, "refresh_token", None),
        "token_uri": creds_dest.token_uri,
        "client_id": creds_dest.client_id,
        "client_secret": creds_dest.client_secret,
        "scopes": creds_dest.scopes,
    }
    st.rerun()

# Si ya está completo, reconstruimos clientes y mostramos RESUMEN (caja verde + link)
drive_service = None
gs_client = None
_me = None

if st.session_state["step1_done"] and st.session_state.get("creds_dest"):
    creds_dest = Credentials(**st.session_state["creds_dest"])
    drive_service, gs_client = ensure_drive_clients(creds_dest)
    _me = get_google_identity(drive_service)
    email_txt = (_me or {}).get("emailAddress") or "email desconocido"

    st.markdown(
        f'''
        <div class="success-inline">
            Los archivos se guardarán en el Drive de: <strong>{email_txt}</strong>
            <a href="?action=change_personal">(Cambiar mail personal)</a>
        </div>
        ''',
        unsafe_allow_html=True
    )

# --- PASO 2: Carpeta destino (opcional) ---
if not st.session_state["step2_done"]:
    st.subheader("2) Destino de la copia (opcional)")
    # show_header=False evita doble título dentro de pick_destination
    dest_folder_id = pick_destination(drive_service, _me, show_header=False)
    st.caption("Si no elegís carpeta, se creará en **Mi unidad**.")
    if st.button("Siguiente ⏭️", key="btn_next_step2"):
        st.session_state["step2_done"] = True
        st.rerun()
else:
    chosen = st.session_state.get("dest_folder_id")
    pretty = "Mi unidad (raíz)" if not chosen else "Carpeta personalizada seleccionada"
    # Resumen en caja verde + LINK (no botón) para cambiar carpeta
    st.markdown(
        f'''
        <div class="success-inline">
            Destino de la copia: <strong>{pretty}</strong>
            <a href="?action=change_folder">(Cambiar carpeta)</a>
        </div>
        ''',
        unsafe_allow_html=True
    )

# --- PASO 3: Conectar Search Console (fuente de datos) ---
sc_service = None
if not st.session_state["step3_done"]:
    # Renderiza UI de SC (elige ACCESO / ACCESO_MEDIOS y autoriza)
    creds_src = pick_source_oauth()
    if not creds_src:
        st.stop()
    # Guardar y colapsar
    st.session_state["creds_src"] = {
        "token": creds_src.token,
        "refresh_token": getattr(creds_src, "refresh_token", None),
        "token_uri": creds_src.token_uri,
        "client_id": creds_src.client_id,
        "client_secret": creds_src.client_secret,
        "scopes": creds_src.scopes,
    }
    # Guardamos también qué cuenta se eligió (el helper suele poblar oauth_src.account)
    src_account = (st.session_state.get("oauth_src") or {}).get("account") or "ACCESO"
    st.session_state["src_account_label"] = src_account
    st.session_state["step3_done"] = True
    st.rerun()
else:
    # Ya autenticado en SC → construir cliente y mostrar resumen en caja verde
    creds_src = Credentials(**st.session_state["creds_src"])
    sc_service = ensure_sc_client(creds_src)
    src_label = st.session_state.get("src_account_label") or "ACCESO"
    st.markdown(
        f'''
        <div class="success-inline">
            Cuenta de acceso (Search Console): <strong>{src_label}</strong>
            <a href="?action=change_src">(Cambiar cuenta de acceso)</a>
        </div>
        ''',
        unsafe_allow_html=True
    )

# --- PASO 4: sitio + PASO 5: análisis ---
site_url = pick_site(sc_service)
analisis = pick_analysis()

# --- Ejecutar ---
if analisis == "4":
    params = params_for_core_update()
    if st.button("🚀 Ejecutar análisis de Core Update", type="primary"):
        sid = run_core_update(sc_service, drive_service, gs_client, site_url, params, st.session_state.get("dest_folder_id"))
        st.success("¡Listo! Tu documento está creado.")
        st.markdown(f"➡️ **Abrir Google Sheets**: https://docs.google.com/spreadsheets/d/{sid}")
        st.session_state["last_file_id"] = sid
        share_controls(drive_service, sid, default_email=_me.get("emailAddress") if _me else None)

elif analisis == "5":
    params = params_for_evergreen()
    if st.button("🌲 Ejecutar análisis Evergreen", type="primary"):
        sid = run_evergreen(sc_service, drive_service, gs_client, site_url, params, st.session_state.get("dest_folder_id"))
        st.success("¡Listo! Tu documento está creado.")
        st.markdown(f"➡️ **Abrir Google Sheets**: https://docs.google.com/spreadsheets/d/{sid}")
        st.session_state["last_file_id"] = sid
        share_controls(drive_service, sid, default_email=_me.get("emailAddress") if _me else None)

elif analisis == "6":
    params = params_for_auditoria()
    if not HAVE_AUDITORIA or run_traffic_audit is None:
        st.warning("Esta opción requiere el paquete externo con `run_traffic_audit` (o alias `run_auditoria`). "
                   "Actualizá tu repo externo y limpiá la caché (.ext_pkgs).")
    else:
        if st.button("📋 Ejecutar auditoría de tráfico", type="primary"):
            sid = run_traffic_audit(
                sc_service, drive_service, gs_client,
                site_url, params, st.session_state.get("dest_folder_id")
            )
            st.success("¡Listo! Tu documento está creado.")
            st.markdown(f"➡️ **Abrir Google Sheets**: https://docs.google.com/spreadsheets/d/{sid}")
            st.session_state["last_file_id"] = sid
            share_controls(drive_service, sid, default_email=_me.get("emailAddress") if _me else None)

else:
    st.info("Las opciones 1, 2 y 3 aún no están disponibles en esta versión.")
