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

# ====== Estilos globales (botones morados + links estilo texto) ======
st.markdown(
    """
<style>
/* Botones morado #8e7cc3 */
.stButton > button, .stDownloadButton > button {
  background: #8e7cc3 !important;
  border-color: #8e7cc3 !important;
  color: #fff !important;
  border-radius: 8px !important;
}
.stButton > button:hover, .stDownloadButton > button:hover {
  filter: brightness(0.93);
}

/* Enlaces-acción que parecen texto (para "Cambiar ...") */
.linkbox button {
  background: transparent !important;
  border: none !important;
  padding: 0 !important;
  color: #5c417c !important;
  text-decoration: underline !important;
  box-shadow: none !important;
}

/* Pills compactas (si en el futuro las usás) */
.pill {
  display:inline-block; padding:.35rem .6rem; border-radius:9999px;
  background:#b4a7d6; color:#222; font-weight:600; font-size:.9rem;
}
</style>
""",
    unsafe_allow_html=True,
)

st.title("Analizador SEO 🚀")

# ====== Utils / paquete externo ======
from modules.utils import debug_log, ensure_external_package

# Resolución de funciones (paquete externo → fallback local)
run_core_update = None
run_evergreen   = None
run_auditoria   = None

_ext = ensure_external_package()
if _ext:
    if hasattr(_ext, "run_core_update"):
        run_core_update = _ext.run_core_update
    if hasattr(_ext, "run_evergreen"):
        run_evergreen = _ext.run_evergreen
    if hasattr(_ext, "run_auditoria"):
        run_auditoria = _ext.run_auditoria

# Fallbacks locales
if run_core_update is None or run_evergreen is None:
    from modules.analysis import run_core_update as _rcu, run_evergreen as _rev  # type: ignore
    if run_core_update is None:
        run_core_update = _rcu
    if run_evergreen is None:
        run_evergreen = _rev

# Auditoría fallback local
if run_auditoria is None:
    try:
        from modules.auditoria import run_auditoria as _ra  # type: ignore
        run_auditoria = _ra
    except Exception:
        run_auditoria = None

USING_EXT = bool(_ext)

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
    st.subheader("5) Elegí el sitio a trabajar (Search Console)")
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
    st.subheader("6) Elegí el tipo de análisis")
    opciones = {
        "1. Análisis de entidades (🚧 próximamente)": "entidades",
        "2. Análisis de tráfico general (🚧 próximamente)": "general",
        "3. Análisis de secciones (🚧 próximamente)": "secciones",
        "4. Análisis de impacto de Core Update ✅": "core",
        "5. Análisis de tráfico evergreen ✅": "evergreen",
        "6. Auditoría de tráfico ✅": "auditoria",
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


def params_for_auditoria():
    st.markdown("#### Parámetros (Auditoría de tráfico)")
    st.caption(
        "Un **período** es la unidad de tiempo que vas a auditar. "
        "Ej.: semanal = 7 días, quincenal ≈ 15 días, mensual ≈ 30 días, o personalizado."
    )
    frecuencia = st.selectbox("Frecuencia", ["Semanal", "Quincenal", "Mensual", "Personalizado"], index=0, key="aud_freq")
    custom_days = None
    if frecuencia == "Personalizado":
        custom_days = st.number_input("Cantidad de días del período personalizado", 1, 90, 7, key="aud_custom_days")

    tipo_datos = st.selectbox("Datos a analizar", ["Search", "Discover", "Ambos"], index=2, key="aud_tipo")
    seccion = st.text_input("¿Limitar a una sección? (path, ej: /vida/)", value="", key="aud_secc") or None
    pais_choice = st.selectbox(
        "¿Ámbito?",
        ["Global", "ARG", "MEX", "ESP", "USA", "COL", "PER", "CHL", "URY"],
        index=0,
        key="aud_pais",
    )
    pais = None if pais_choice == "Global" else pais_choice
    num_previos = st.number_input("¿Cuántos periodos previos querés comparar?", 1, 12, 4, key="aud_prev")
    lag_days = st.number_input("Lag de datos (para evitar días incompletos)", 0, 7, LAG_DAYS_DEFAULT, key="aud_lag")

    return dict(
        frecuencia=frecuencia,
        tipo_datos=tipo_datos,
        seccion=seccion,
        pais=pais,
        num_previos=int(num_previos),
        custom_days=int(custom_days) if custom_days else None,
        lag_days=int(lag_days),
    )


# ============== App ==============
user = get_user()
if not user or not getattr(user, "is_logged_in", False):
    login_screen()
    st.stop()

# Sidebar → Mantenimiento: mensaje del paquete y modo debug
def maintenance_extra_ui(using_ext: bool = USING_EXT):
    if using_ext:
        st.caption("🧩 Usando análisis del paquete externo (repo privado).")
    else:
        st.caption("🧩 Usando análisis embebidos en este repo.")
    st.checkbox("🔧 Modo debug (Drive/GSC)", key="DEBUG")

sidebar_user_info(user, maintenance_extra=maintenance_extra_ui)

# Estados de pasos
st.session_state.setdefault("step1_done", False)  # Personal Drive/Sheets conectado
st.session_state.setdefault("step2_done", False)  # Carpeta destino elegida (opcional)
st.session_state.setdefault("step3_done", False)  # Cuenta de SC conectada

# --- PASO 1: OAuth PERSONAL (Drive/Sheets) ---
creds_dest = None
if not st.session_state["step1_done"]:
    # Deja que pick_destination_oauth renderice su UI (evita título duplicado)
    creds_dest = pick_destination_oauth()
    if not creds_dest:
        st.stop()
    # Guardamos y colapsamos
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

# Si ya está completo, reconstruimos clientes y mostramos RESUMEN
drive_service = None
gs_client = None
_me = None

if st.session_state["step1_done"] and st.session_state.get("creds_dest"):
    creds_dest = Credentials(**st.session_state["creds_dest"])
    drive_service, gs_client = ensure_drive_clients(creds_dest)
    _me = get_google_identity(drive_service)

    email_txt = (_me or {}).get("emailAddress") or "email desconocido"
    # Caja de éxito + link "cambiar"
    col_l, col_r = st.columns([4, 1])
    with col_l:
        st.success(f"Los archivos se guardarán en el Drive de: **{email_txt}**")
    with col_r:
        st.markdown('<div class="linkbox">', unsafe_allow_html=True)
        if st.button("(Cambiar mail personal)", key="link_change_personal"):
            for k in ("creds_dest", "oauth_dest", "step1_done"):
                st.session_state.pop(k, None)
            st.session_state["step2_done"] = False
            st.session_state.pop("dest_folder_id", None)
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

# --- PASO 2: Carpeta destino (opcional) ---
if not st.session_state["step2_done"]:
    # No agregamos nuestro subheader para evitar duplicar el título:
    # pick_destination ya muestra "Destino de la copia (opcional)"
    dest_folder_id = pick_destination(drive_service, _me)  # guarda internamente en session_state["dest_folder_id"]
    st.caption("Si no elegís carpeta, se creará en **Mi unidad**.")
    if st.button("Siguiente ⏭️", key="btn_next_step2"):
        st.session_state["step2_done"] = True
        st.rerun()
else:
    chosen = st.session_state.get("dest_folder_id")
    pretty = "Mi unidad (raíz)" if not chosen else "Carpeta personalizada seleccionada"
    col_l2, col_r2 = st.columns([4, 1])
    with col_l2:
        st.success(f"Destino de la copia: **{pretty}**")
    with col_r2:
        st.markdown('<div class="linkbox">', unsafe_allow_html=True)
        if st.button("(Cambiar carpeta)", key="link_change_folder"):
            st.session_state["step2_done"] = False
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

# --- PASO 3: Conectar Search Console (fuente de datos) ---
creds_src = None
if not st.session_state["step3_done"]:
    creds_src = pick_source_oauth()
    if not creds_src:
        st.stop()
    st.session_state["creds_src"] = {
        "token": creds_src.token,
        "refresh_token": getattr(creds_src, "refresh_token", None),
        "token_uri": creds_src.token_uri,
        "client_id": creds_src.client_id,
        "client_secret": creds_src.client_secret,
        "scopes": creds_src.scopes,
    }
    st.session_state["step3_done"] = True
    st.rerun()
else:
    acct = st.session_state.get("oauth_src", {}).get("account", "Cuenta SC")
    col_l3, col_r3 = st.columns([4, 1])
    with col_l3:
        st.success(f"Cuenta de Search Console conectada: **{acct}**")
    with col_r3:
        st.markdown('<div class="linkbox">', unsafe_allow_html=True)
        if st.button("(Cambiar cuenta)", key="link_change_sc"):
            for k in ("creds_src", "oauth_src"):
                st.session_state.pop(k, None)
            st.session_state["step3_done"] = False
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

# Cliente de Search Console con las credenciales guardadas
creds_src = Credentials(**st.session_state["creds_src"])
sc_service = ensure_sc_client(creds_src)

# --- PASO 4: sitio + análisis ---
site_url = pick_site(sc_service)
analisis = pick_analysis()

# --- PASO 5: ejecutar ---
if analisis == "core":
    params = params_for_core_update()
    if st.button("🚀 Ejecutar análisis de Core Update", type="primary"):
        sid = run_core_update(sc_service, drive_service, gs_client, site_url, params, st.session_state.get("dest_folder_id"))
        st.success("¡Listo! Tu documento está creado.")
        st.markdown(f"➡️ **Abrir Google Sheets**: https://docs.google.com/spreadsheets/d/{sid}")
        st.session_state["last_file_id"] = sid
        share_controls(drive_service, sid, default_email=_me.get("emailAddress") if _me else None)

elif analisis == "evergreen":
    params = params_for_evergreen()
    if st.button("🌲 Ejecutar análisis Evergreen", type="primary"):
        sid = run_evergreen(sc_service, drive_service, gs_client, site_url, params, st.session_state.get("dest_folder_id"))
        st.success("¡Listo! Tu documento está creado.")
        st.markdown(f"➡️ **Abrir Google Sheets**: https://docs.google.com/spreadsheets/d/{sid}")
        st.session_state["last_file_id"] = sid
        share_controls(drive_service, sid, default_email=_me.get("emailAddress") if _me else None)

elif analisis == "auditoria":
    params = params_for_auditoria()
    if st.button("🧪 Ejecutar Auditoría de tráfico", type="primary"):
        if run_auditoria is None:
            st.error("Este despliegue no incluye `run_auditoria` (paquete externo o fallback local).")
        else:
            sid = run_auditoria(sc_service, drive_service, gs_client, site_url, params, st.session_state.get("dest_folder_id"))
            st.success("¡Listo! Tu documento está creado.")
            st.markdown(f"➡️ **Abrir Google Sheets**: https://docs.google.com/spreadsheets/d/{sid}")
            st.session_state["last_file_id"] = sid
            share_controls(drive_service, sid, default_email=_me.get("emailAddress") if _me else None)

else:
    st.info("Las opciones 1, 2 y 3 aún no están disponibles en esta versión.")
