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
# Logo fijo (no desaparece) y autoalineación con contenedor principal
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
enable_brand_auto_align()

# ====== Estilos globales: botones morados + links estilo texto + “caja verde” con link dentro ======
st.markdown("""
<style>
/* Botones morado #8e7cc3 */
.stButton > button, .stDownloadButton > button {
  background: #8e7cc3 !important;
  border-color: #8e7cc3 !important;
  color: #fff !important;
  border-radius: 8px !important;
}
.stButton > button:hover, .stDownloadButton > button:hover { filter: brightness(0.93); }

/* Link-acción (para "Cambiar ...") con apariencia de enlace */
.linkbox button {
  background: transparent !important;
  border: none !important;
  padding: 0 !important;
  color: #1a73e8 !important;        /* azul link */
  text-decoration: underline !important;
  box-shadow: none !important;
}

/* Caja de éxito verde personalizada (para tener el link adentro) */
.success-card {
  background: #d1fae5;            /* verde claro */
  border: 1px solid #10b981;      /* borde verde */
  border-radius: 8px;
  padding: 10px 12px;
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 0 0 8px 0;
}
.success-card .sc-text { flex: 1 1 auto; font-weight: 600; color: #065f46; }
.success-card .sc-action { flex: 0 0 auto; }
.success-card .sc-action button { color: #065f46 !important; text-decoration: underline !important; }
</style>
""", unsafe_allow_html=True)

st.title("Analizador SEO 🚀")

# ====== Utils / paquete externo ======
from modules.utils import debug_log, ensure_external_package

# Inicializamos referencias vacías
run_core_update = run_evergreen = run_auditoria = None

# 1) Intentar cargar desde el paquete externo (repo privado)
_ext = ensure_external_package()
if _ext:
    run_core_update = getattr(_ext, "run_core_update", None)
    run_evergreen = getattr(_ext, "run_evergreen", None)
    run_auditoria = getattr(_ext, "run_auditoria", None)

# 2) Si no están (o faltan algunas), intentar fallback local dinámico
if not (run_core_update and run_evergreen):
    try:
        import importlib
        analysis = importlib.import_module("modules.analysis")
        run_core_update = getattr(analysis, "run_core_update", run_core_update)
        run_evergreen  = getattr(analysis, "run_evergreen",  run_evergreen)
        run_auditoria  = getattr(analysis, "run_auditoria",  run_auditoria)
        USING_EXT = False
    except Exception as e:
        st.error(
            "No pude cargar las funciones de análisis ni del paquete externo ni de `modules/analysis.py`.\n\n"
            "✔️ Verificá que exista `modules/analysis.py` con `run_core_update` y `run_evergreen`, "
            "o configurá correctamente el paquete externo."
        )
        st.caption(f"Detalle técnico: {e}")
        st.stop()
else:
    USING_EXT = True

# 👉 GUARDA el flag en session_state para que esté disponible en cualquier callback
st.session_state["USING_EXT"] = USING_EXT


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
    opciones = {
        "1. Análisis de entidades (🚧 próximamente)": "1",
        "2. Análisis de tráfico general (🚧 próximamente)": "2",
        "3. Análisis de secciones (🚧 próximamente)": "3",
        "4. Análisis de impacto de Core Update ✅": "4",
        "5. Análisis de tráfico evergreen ✅": "5",
        "6. Auditoría de tráfico ✅": "6",
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


# --- Ayuda: explicación del “período” para Auditoría ---
def _explica_periodo(freq: str, custom_days: int | None = None) -> str:
    if freq == "Semanal":
        return "Un **período** = 7 días consecutivos. Se toma como referencia hoy menos el lag elegido."
    if freq == "Quincenal":
        return "Un **período** = 15 días consecutivos."
    if freq == "Mensual":
        return "Un **período** = 1 mes calendario completo que contiene el día de referencia (hoy - lag), y los meses anteriores."
    # Personalizado
    return f"Un **período** = {custom_days or 7} días consecutivos."


def params_for_auditoria():
    st.markdown("#### Parámetros (Auditoría de tráfico)")

    modo = st.selectbox("Frecuencia del período", ["Semanal", "Quincenal", "Mensual", "Personalizado"], index=0, key="aud_modo")
    tipo = st.selectbox("Origen de datos", ["Search", "Discover", "Search y Discover"], index=2, key="aud_tipo")
    seccion = st.text_input("¿Limitar a una sección? (path, ej: /vida/)", value="", key="aud_seccion") or None
    alcance = st.selectbox("Ámbito", ["Global", "País"], index=0, key="aud_ambito")

    country = None
    if alcance == "País":
        country = st.text_input("ISO-3 del país (ej: ARG, MEX, ESP)", value="", key="aud_iso3").strip().upper() or None

    lag_days = st.number_input("Lag de datos (para evitar días incompletos)", 0, 7, 3, key="aud_lag")

    custom_days = None
    if modo == "Personalizado":
        custom_days = st.number_input("Duración del período personalizado (en días)", min_value=2, max_value=120, value=7, key="aud_custom_days")

    periods_back = st.number_input(
        "¿Cuántos períodos **previos** querés comparar?",
        min_value=1, max_value=24, value=4, step=1, key="aud_periods_back",
        help="Por defecto 4. El análisis abarcará el período actual + N períodos previos."
    )

    st.caption(_explica_periodo(modo, custom_days))
    return modo, tipo, seccion, alcance, country, lag_days, (custom_days or 0), periods_back


# ============== App ==============
user = get_user()
if not user or not getattr(user, "is_logged_in", False):
    login_screen()
    st.stop()

# Sidebar → Mantenimiento: mensaje del paquete y modo debug
def maintenance_extra_ui(USING_EXT: bool = st.session_state.get("USING_EXT", False)):
    """Bloque 'Mantenimiento' en el sidebar: mensaje de origen de análisis + toggle debug."""
    if USING_EXT:
        st.caption("🧩 Usando análisis del paquete externo (repo privado).")
    else:
        st.caption("🧩 Usando análisis embebidos en este repo.")
    st.checkbox("🔧 Modo debug (Drive/GSC)", key="DEBUG")

sidebar_user_info(user, maintenance_extra=maintenance_extra_ui)

# Estados de pasos
st.session_state.setdefault("step1_done", False)   # PERSONAL
st.session_state.setdefault("step2_done", False)   # CARPETA
st.session_state.setdefault("step3_done", False)   # SOURCE (SC)

# ---------- PASO 1: OAuth PERSONAL (Drive/Sheets) ----------
creds_dest = None
if not st.session_state["step1_done"]:
    # Renderiza UI interna del módulo (sin duplicar título aquí)
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

# Resumen “caja verde” de paso 1 + link interno
drive_service = None
gs_client = None
_me = None
if st.session_state["step1_done"] and st.session_state.get("creds_dest"):
    creds_dest = Credentials(**st.session_state["creds_dest"])
    drive_service, gs_client = ensure_drive_clients(creds_dest)
    _me = get_google_identity(drive_service)
    email_txt = (_me or {}).get("emailAddress") or "email desconocido"

    # Caja verde con link “Cambiar mail personal” adentro
    colA, colB = st.columns([1, 1], gap="small")
    with colA:
        st.markdown(
            f"""
            <div class="success-card">
              <div class="sc-text">Los archivos se guardarán en el Drive de: <strong>{email_txt}</strong></div>
              <div class="sc-action"><div class="linkbox">
                <form>
                  <button name="link_change_personal" type="submit">Cambiar mail personal</button>
                </form>
              </div></div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    # “Captura” del submit como si fuera un botón
    if st.query_params.get("link_change_personal") or st.session_state.get("link_change_personal"):
        for k in ("creds_dest", "oauth_dest", "step1_done"):
            st.session_state.pop(k, None)
        st.session_state["step2_done"] = False
        st.session_state.pop("dest_folder_id", None)
        st.session_state["link_change_personal"] = False
        st.rerun()
    # Botón invisible para manejar el “submit” del form como evento
    st.session_state["link_change_personal"] = st.button(" ", key="__ghost1__", help="ghost", disabled=True)

# ---------- PASO 2: Carpeta destino (opcional) ----------
if not st.session_state["step2_done"]:
    st.subheader("2) Destino de la copia (opcional)")
    # UI para elegir carpeta (usa la cuenta personal ya conectada)
    dest_folder_id = pick_destination(drive_service, _me)  # guarda internamente en session_state["dest_folder_id"]
    st.caption("Si no elegís carpeta, se creará en **Mi unidad**.")
    if st.button("Siguiente ⏭️", key="btn_next_step2"):
        st.session_state["step2_done"] = True
        st.rerun()
else:
    chosen = st.session_state.get("dest_folder_id")
    pretty = "Mi unidad (raíz)" if not chosen else "Carpeta personalizada seleccionada"
    # Caja verde con link “Cambiar carpeta” adentro
    st.markdown(
        f"""
        <div class="success-card">
          <div class="sc-text">Destino de la copia: <strong>{pretty}</strong></div>
          <div class="sc-action"><div class="linkbox">
            <form>
              <button name="link_change_folder" type="submit">Cambiar carpeta</button>
            </form>
          </div></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if st.query_params.get("link_change_folder") or st.session_state.get("link_change_folder"):
        st.session_state["step2_done"] = False
        st.session_state["link_change_folder"] = False
        st.rerun()
    st.session_state["link_change_folder"] = st.button(" ", key="__ghost2__", help="ghost", disabled=True)

# ---------- PASO 3: Conectar Search Console (fuente de datos) ----------
creds_src = None
if not st.session_state["step3_done"]:
    # La UI de pick_source_oauth incluye el selector ACCESO/ACCESO_MEDIOS y autorización
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
    # Resumen del paso 3 (no sabemos email de SC si es multi-tenant, mostramos client_id)
    client_id = (st.session_state.get("creds_src") or {}).get("client_id", "—")
    st.markdown(
        f"""
        <div class="success-card">
          <div class="sc-text">Fuente de datos (Search Console): <strong>conectada</strong></div>
          <div class="sc-action"><div class="linkbox">
            <form>
              <button name="link_change_sc" type="submit">Cambiar cuenta SC</button>
            </form>
          </div></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if st.query_params.get("link_change_sc") or st.session_state.get("link_change_sc"):
        for k in ("creds_src", "oauth_src", "step3_done"):
            st.session_state.pop(k, None)
        st.session_state["link_change_sc"] = False
        st.rerun()
    st.session_state["link_change_sc"] = st.button(" ", key="__ghost3__", help="ghost", disabled=True)

# Cliente SC listo
creds_src = Credentials(**st.session_state["creds_src"])
sc_service = ensure_sc_client(creds_src)

# ---------- PASO 4: sitio ----------
site_url = pick_site(sc_service)

# ---------- PASO 5: análisis ----------
analisis = pick_analysis()

# ---------- PASO 6: ejecutar ----------
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
    if run_auditoria is None:
        st.error("Este despliegue no incluye `run_auditoria` (paquete externo o fallback local).")
    else:
        params = params_for_auditoria()
        if st.button("🧪 Ejecutar Auditoría de tráfico", type="primary"):
            sid = run_auditoria(sc_service, drive_service, gs_client, site_url, params, st.session_state.get("dest_folder_id"))
            st.success("¡Listo! Tu documento de Auditoría está creado.")
            st.markdown(f"➡️ **Abrir Google Sheets**: https://docs.google.com/spreadsheets/d/{sid}")
            st.session_state["last_file_id"] = sid
            share_controls(drive_service, sid, default_email=_me.get("emailAddress") if _me else None)

else:
    st.info("Las opciones 1, 2 y 3 aún no están disponibles en esta versión.")
