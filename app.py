from __future__ import annotations

# --- Permisos OAuth en localhost + tolerancia de scope
import os
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

import json
import sys
from types import SimpleNamespace
from datetime import date as _date, timedelta as _td

import pandas as pd
import streamlit as st
from google.oauth2.credentials import Credentials

# ====== Config base ======
try:
    st.set_page_config(layout="wide", page_title="Análisis SEO", page_icon="📊")
except Exception:
    pass

# ---- Shims de compatibilidad
for _name in [
    "app_constants","app_config","app_ext","app_utils","app_params",
    "app_errors","app_activity","app_auth_flow","app_diagnostics","app_ai",
]:
    try:
        _mod = __import__(f"modules.{_name}", fromlist=["*"])
        sys.modules.setdefault(_name, _mod)
    except Exception:
        pass

# ====== UI / Branding ======
from modules.ui import apply_page_style, get_user, sidebar_user_info, login_screen

# ====== Carga de módulos locales ======
from modules.app_config import apply_base_style_and_logo, get_app_home
from modules.app_ext import USING_EXT, run_core_update, run_evergreen, run_traffic_audit, run_names_analysis

# Discover Snoop (repo externo) — proteger import por si no está disponible
try:
    from modules.app_ext import run_discover_snoop
except Exception:
    run_discover_snoop = None  # fallback

# NUEVO: Análisis de contenido (repo externo) — proteger import
try:
    from modules.app_ext import run_content_analysis
except Exception:
    run_content_analysis = None  # fallback

from modules.app_utils import get_qp, clear_qp, has_gsc_scope, norm
from modules.app_ai import load_prompts, gemini_healthcheck, gemini_summary
from modules.app_params import (
    params_for_core_update, params_for_evergreen, params_for_auditoria, params_for_names,
)

# Params para Discover Snoop — proteger import
try:
    from modules.app_params import params_for_discover_snoop
except Exception:
    params_for_discover_snoop = lambda: {}

# NUEVO: params del análisis de contenido — proteger import
try:
    from modules.app_params import params_for_content
except Exception:
    params_for_content = None

from modules.app_activity import maybe_prefix_sheet_name_with_medio, activity_log_append
from modules.app_errors import run_with_indicator
from modules.app_auth_flow import step0_google_identity, logout_screen
from modules.app_diagnostics import scan_repo_for_gsc_and_filters, read_context

# 🔑 para leer tokens guardados por el Paso 0 en otra pestaña
from modules.utils import token_store

# ====== Google modules ======
from modules.drive import ensure_drive_clients, get_google_identity, pick_destination, share_controls
from modules.gsc import ensure_sc_client

# ====== Estilo / branding ======
apply_base_style_and_logo()

# ⬇️ Sin espacios arriba + logo que acompaña al sidebar (solo CSS)
st.markdown("""
<style>
/* 0) Remover cualquier “banda” o espaciador que pueda dejar la capa de estilo base */
#nmd-band, .nmd-band, [data-nmd="band"], [id*="band"], [class*="band"] {
  display: none !important; height:0 !important; margin:0 !important; padding:0 !important;
}

/* 1) Quitar padding/margen superior que Streamlit agrega por el header */
div[data-testid="stAppViewContainer"] { padding-top: 0 !important; }
main .block-container { margin-top: 0 !important; padding-top: .75rem !important; }

/* 2) Asegurar el header por encima */
header[data-testid="stHeader"] { z-index: 1500 !important; }

/* 3) Mover el logo del header para que siga al sidebar (el logo lo inyecta app_config.py con ::before) */

/* Sidebar ABIERTO (ajusta 288–304px si tu sidebar es distinto) */
:root:has([data-testid="stSidebar"][aria-expanded="true"])
  header[data-testid="stHeader"]::before {
  left: 350px !important;
}

/* Sidebar CERRADO */
:root:has([data-testid="stSidebar"][aria-expanded="false"])
  header[data-testid="stHeader"]::before {
  left: 100px !important;
}

/* Fallback por si no existe el atributo en alguna versión */
:root:not(:has([data-testid="stSidebar"])) header[data-testid="stHeader"]::before {
  left: 16px !important;
}
</style>
""", unsafe_allow_html=True)

st.title("Analizador SEO 🚀")

# ---------- IA / Prompts ----------
load_prompts()
if not st.session_state.get("DEBUG"):
    try:
        ok, _ = gemini_healthcheck()
        if not ok:
            st.caption("💡 Podés cargar una API key de Gemini en Secrets (GEMINI_API_KEY o [gemini].api_key).")
    except Exception:
        pass

# ============== App ==============

APP_HOME = get_app_home()

# Detectar pantalla de logout por query param
_view = get_qp().get("view")
if isinstance(_view, list):
    _view = _view[0] if _view else None
if _view == "logout":
    logout_screen(APP_HOME)
    st.stop()

# Preferir Paso 0 (OIDC + Drive/Sheets + GSC en un solo botón)
prefer_oidc = bool(st.secrets.get("auth", {}).get("prefer_oidc", True))

ident = st.session_state.get("_google_identity")
user = get_user()

# Si había bypass y preferimos OIDC, forzamos Paso 0
if prefer_oidc and st.session_state.get("_auth_bypass"):
    st.session_state.pop("_auth_bypass", None)
    user = None

# --- PASO 0: Login botón Google (web) ---
if prefer_oidc and not ident:
    ident = step0_google_identity()  # guarda st.session_state["creds_dest"] y token_store["creds_dest"]
    if not ident:
        st.stop()

# Si no hay user de Streamlit, creamos uno con la identidad OIDC
if not user:
    if ident:
        user = SimpleNamespace(
            is_logged_in=True,
            name=(ident.get("name") or "Invitado"),
            email=(ident.get("email") or "—"),
            picture=(ident.get("picture")),
        )
    else:
        login_screen()
        st.stop()

# Sidebar → mantenimiento
def maintenance_extra_ui():
    if USING_EXT:
        st.caption("🧩 Usando análisis del paquete externo (repo privado).")
    else:
        st.caption("🧩 Usando análisis embebidos en este repo.")
    st.checkbox("🔧 Modo debug (Drive/GSC)", key="DEBUG")

sidebar_user_info(user, maintenance_extra=maintenance_extra_ui)

# === Acciones de query ===
_qp = get_qp()
_action = _qp.get("action")
if isinstance(_action, list):
    _action = _action[0] if _action else None

if _action == "change_personal":
    # Reiniciar el login personal del Paso 0
    for k in ("oauth_oidc","_google_identity","creds_dest"):
        st.session_state.pop(k, None)
    try:
        token_store.clear("creds_dest")
    except Exception:
        pass
    clear_qp(); st.rerun()
elif _action == "change_folder":
    st.session_state.pop("dest_folder_id", None)
    clear_qp(); st.rerun()
elif _action == "change_src":
    for k in ("creds_src", "step3_done", "src_account_label"):
        st.session_state.pop(k, None)
    st.session_state.pop("sc_account_choice", None)
    try:
        token_store.clear("creds_src")
    except Exception:
        pass
    clear_qp(); st.rerun()

# --- Inicializar Drive/Sheets usando LAS CREDENCIALES del Paso 0 ---
drive_service = None
gs_client = None
_me = None

# 1) Intentar desde session_state
creds_dest = None
if st.session_state.get("creds_dest"):
    try:
        creds_dest = Credentials(**st.session_state["creds_dest"])
    except Exception:
        creds_dest = None

# 2) Fallback cross-pestaña: token_store (si el login se hizo en otra pestaña)
if not creds_dest:
    try:
        creds_dest = token_store.as_credentials("creds_dest")
        if creds_dest:
            # hidratar también el session_state para el resto del flujo
            st.session_state["creds_dest"] = {
                "token": creds_dest.token,
                "refresh_token": getattr(creds_dest, "refresh_token", None),
                "token_uri": creds_dest.token_uri,
                "client_id": creds_dest.client_id,
                "client_secret": creds_dest.client_secret,
                "scopes": list(getattr(creds_dest, "scopes", [])),
            }
    except Exception:
        creds_dest = None

if not creds_dest:
    st.error(
        "No recibí credenciales personales tras el Paso 0. "
        "Volvé a pulsar **Iniciar sesión con Google** (un solo click)."
    )
    st.stop()

# Clientes Google Drive/Sheets y resumen de identidad
try:
    drive_service, gs_client = ensure_drive_clients(creds_dest)
    _me = get_google_identity(drive_service)
    # Refrescar identidad por si llega con foto/nombre
    st.session_state["_google_identity"] = _me or st.session_state.get("_google_identity", {})
    email_txt = (_me or {}).get("emailAddress") or "email desconocido"
    st.markdown(
        f'''
        <div class="success-inline">
            Sesión personal: <strong>{email_txt}</strong>
            <a href="{APP_HOME}?action=change_personal" target="_self" rel="nofollow">(Cambiar cuenta personal)</a>
        </div>
        ''',
        unsafe_allow_html=True
    )
    activity_log_append(
        drive_service, gs_client,
        user_email=email_txt, event="login",
        gsc_account=st.session_state.get("src_account_label") or "",
        notes="Paso 0 OK (OIDC + Drive/Sheets + GSC)"
    )
except Exception as e:
    st.error(f"No pude inicializar Drive/Sheets con la cuenta personal: {e}")
    st.stop()

# --- Carpeta destino (opcional) ---
if "step2_done" not in st.session_state:
    st.session_state["step2_done"] = False

if not st.session_state["step2_done"]:
    with st.expander("2) Destino de la copia (opcional)", expanded=False):
        st.caption("Por defecto el archivo se guardará en **Mi unidad (raíz)**. "
                   "Si querés otra carpeta, abrí este panel y elegila aquí.")
        dest_folder_id = pick_destination(drive_service, _me, show_header=False)
        c1, c2 = st.columns([1, 3])
        with c1:
            if st.button("Guardar selección", key="btn_save_step2"):
                st.session_state["step2_done"] = True
                st.rerun()
        with c2:
            st.caption("Podés dejar este paso cerrado para usar **Mi unidad** por defecto.")
else:
    chosen = st.session_state.get("dest_folder_id")
    pretty = "Mi unidad (raíz)" if not chosen else "Carpeta personalizada seleccionada"
    st.markdown(
        f'''
        <div class="success-inline">
            Destino de la copia: <strong>{pretty}</strong>
            <a href="{APP_HOME}?action=change_folder" target="_self" rel="nofollow">(Cambiar carpeta)</a>
        </div>
        ''',
        unsafe_allow_html=True
    )

# ---------- Elegir análisis ----------
include_auditoria = run_traffic_audit is not None
def pick_analysis(include_auditoria: bool, include_names: bool = True, include_discover: bool = True, include_content: bool = True):
    st.subheader("¿Qué tipo de análisis quieres realizar?")
    opciones = [
        "1. Análisis de entidades (🚧 próximamente)",
        "2. Análisis de tráfico general (🚧 próximamente)",
        "3. Análisis de secciones (🚧 próximamente)",
        "4. Análisis de impacto de Core Update ✅",
        "5. Análisis de tráfico evergreen ✅",
    ]
    if include_auditoria:
        opciones.append("6. Auditoría de tráfico ✅")
    if include_names:
        opciones.append("7. Análisis de Nombres (KG + Wikipedia) ✅")
    if include_discover:
        opciones.append("8. Análisis en base a Discover Snoop ✅")
    if include_content:
        opciones.append("9. Análisis de contenido ✅")

    key = st.radio("Tipos disponibles:", opciones, index=3, key="analysis_choice")
    if key.startswith("4."): return "4"
    if key.startswith("5."): return "5"
    if key.startswith("6."): return "6"
    if key.startswith("7."): return "7"
    if key.startswith("8."): return "8"
    if key.startswith("9."): return "9"
    return "0"

analisis = pick_analysis(include_auditoria, include_names=True, include_discover=True, include_content=True)

# ---------- Rama especial: Nombres (no usa GSC) ----------
if analisis == "7":
    if run_names_analysis is None:
        st.warning("Este despliegue no incluye `run_names_analysis`.")
    else:
        params_names = params_for_names()
        total = len(params_names.get("items") or [])
        if total == 0:
            st.info("Cargá un CSV o pegá al menos un nombre para habilitar la ejecución.")
        else:
            if st.button("🔎 Ejecutar Análisis de Nombres (KG + Wikipedia)", type="primary"):
                sid = run_with_indicator(
                    "Procesando Análisis de Nombres (KG + Wikipedia)",
                    run_names_analysis, drive_service, gs_client,
                    params_names, st.session_state.get("dest_folder_id")
                )
                st.success("¡Listo! Tu documento está creado.")
                st.markdown(f"➡️ **Abrir Google Sheets**: https://docs.google.com/spreadsheets/d/{sid}")
                with st.expander("Compartir acceso al documento (opcional)"):
                    share_controls(drive_service, sid, default_email=_me.get("emailAddress") if _me else None)
                try:
                    meta = drive_service.files().get(fileId=sid, fields="name,webViewLink").execute()
                    sheet_name = meta.get("name", ""); sheet_url = meta.get("webViewLink") or f"https://docs.google.com/spreadsheets/d/{sid}"
                except Exception:
                    sheet_name = ""; sheet_url = f"https://docs.google.com/spreadsheets/d/{sid}"
                activity_log_append(
                    drive_service, gs_client,
                    user_email=(_me or {}).get("emailAddress") or "",
                    event="analysis", site_url="",
                    analysis_kind="Nombres (KG+Wikipedia)",
                    sheet_id=sid, sheet_name=sheet_name, sheet_url=sheet_url,
                    gsc_account="", notes=f"lang={params_names.get('lang')}, n={total}"
                )
                st.session_state["last_file_id"] = sid
                st.session_state["last_file_kind"] = "names"
    if st.session_state.get("last_file_id") and st.session_state.get("last_file_kind"):
        st.divider(); st.subheader("📄 Resumen del análisis")
        st.caption("Podés generar o regenerar el resumen sin volver a ejecutar el análisis.")
        gemini_summary(gs_client, st.session_state["last_file_id"],
                       kind=st.session_state["last_file_kind"], widget_suffix="panel")
    st.stop()

# ---------- Rama especial: Discover Snoop (no usa GSC) ----------
if analisis == "8":
    if run_discover_snoop is None:
        st.warning("Este despliegue no incluye `run_discover_snoop` (repo externo).")
    else:
        st.subheader("Subí el CSV exportado de Discover Snoop")
        up = st.file_uploader("Archivo CSV", type=["csv"])
        params_ds = params_for_discover_snoop()

        with st.expander("Formato esperado (campos mínimos)"):
            st.markdown("""
            Debe contener **publisher, title, url, category, firstviewed, lastviewed**.  
            `entities` es opcional pero recomendado.
            """)

        df = None
        if up is not None:
            try:
                df = pd.read_csv(up)
            except Exception:
                up.seek(0)
                df = pd.read_csv(up, encoding="latin-1")
            st.success(f"CSV cargado: {len(df):,} filas")

        if df is None:
            st.info("Cargá el CSV para habilitar la ejecución.")
        else:
            if st.button("🔎 Ejecutar Análisis Discover Snoop", type="primary"):
                sid = run_with_indicator(
                    "Procesando Discover Snoop",
                    run_discover_snoop,  # función del paquete externo
                    drive_service, gs_client,  # servicios Google
                    df, params_ds,            # datos + parámetros
                    st.session_state.get("dest_folder_id")
                )
                st.success("¡Listo! Tu documento está creado.")
                st.markdown(f"➡️ **Abrir Google Sheets**: https://docs.google.com/spreadsheets/d/{sid}")
                with st.expander("Compartir acceso al documento (opcional)"):
                    share_controls(drive_service, sid, default_email=_me.get("emailAddress") if _me else None)
                try:
                    meta = drive_service.files().get(fileId=sid, fields="name,webViewLink").execute()
                    sheet_name = meta.get("name", ""); sheet_url = meta.get("webViewLink") or f"https://docs.google.com/spreadsheets/d/{sid}"
                except Exception:
                    sheet_name = ""; sheet_url = f"https://docs.google.com/spreadsheets/d/{sid}"
                activity_log_append(
                    drive_service, gs_client,
                    user_email=(_me or {}).get("emailAddress") or "",
                    event="analysis", site_url="",
                    analysis_kind="Discover Snoop",
                    sheet_id=sid, sheet_name=sheet_name, sheet_url=sheet_url,
                    gsc_account="", notes=f"params={params_ds!r}"
                )
                st.session_state["last_file_id"] = sid
                st.session_state["last_file_kind"] = "discover"

    if st.session_state.get("last_file_id") and st.session_state.get("last_file_kind"):
        st.divider(); st.subheader("📄 Resumen del análisis")
        st.caption("Podés generar o regenerar el resumen sin volver a ejecutar el análisis.")
        gemini_summary(gs_client, st.session_state["last_file_id"],
                       kind=st.session_state["last_file_kind"], widget_suffix="panel")
    st.stop()

# ======== Resto de análisis (requieren GSC) ========

# --- Builder local para flujo 'installed' (SC cuentas ACCESO / ACCESO_MEDIOS)
def _build_flow_installed_or_local(account_key: str, scopes: list[str]):
    from google_auth_oauthlib.flow import Flow
    acc = (st.secrets.get("accounts") or {}).get(account_key) or {}
    if not (acc.get("client_id") and acc.get("client_secret")):
        st.error(f"No encontré credenciales en secrets['accounts']['{account_key}'] (client_id/client_secret).")
        st.stop()
    client_secrets = {
        "installed": {
            "client_id": acc["client_id"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_secret": acc["client_secret"],
            "redirect_uris": ["http://localhost"],
        }
    }
    flow = Flow.from_client_config(client_secrets, scopes=scopes)
    flow.redirect_uri = "http://localhost"
    return flow

GSC_SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]

def pick_source_oauth_forced(account_key: str) -> Credentials | None:
    """
    Igual a un pick_source_oauth pero fijando la cuenta (sin radios).
    """
    st.subheader("Cuenta de Search Console (fuente de datos)")
    key = f"oauth_src_{account_key}"
    if key not in st.session_state:
        flow = _build_flow_installed_or_local(account_key, GSC_SCOPES)
        auth_url, state = flow.authorization_url(
            prompt="consent select_account",
            access_type="offline",
        )
        st.session_state[key] = {"account": account_key, "flow": flow, "auth_url": auth_url, "state": state}

    osrc = st.session_state[key]
    st.markdown(f"🔗 **Autorizar acceso a Search Console** → {osrc['auth_url']}")
    with st.expander("Ver/copiar URL de autorización (fuente)"):
        st.code(osrc["auth_url"])

    url = st.text_input(
        "Pegá la URL de redirección (http://localhost/?code=...&state=...)",
        key=f"auth_response_url_src_{account_key}",
        placeholder="http://localhost/?code=...&state=...",
    )

    c1, c2 = st.columns([1,1])
    creds = None
    with c1:
        if st.button("Conectar Search Console", key=f"btn_connect_src_{account_key}", type="secondary"):
            if not url.strip():
                st.error("Pegá la URL completa de redirección (incluye code y state)."); st.stop()
            from urllib.parse import urlsplit, parse_qs
            try:
                qs = parse_qs(urlsplit(url.strip()).query)
                returned_state = (qs.get("state") or [""])[0]
            except Exception:
                returned_state = ""
            expected_state = osrc.get("state")
            if not returned_state or returned_state != expected_state:
                st.error("CSRF Warning: el 'state' devuelto no coincide con el generado.")
                st.stop()
            try:
                flow = osrc["flow"]
                flow.fetch_token(authorization_response=url.strip())
                creds = flow.credentials
            except Exception as e:
                st.error("No se pudo conectar Search Console. Reintentá la autorización.")
                st.caption(f"Detalle técnico: {e}")
    with c2:
        if st.button("Reiniciar Paso 2", key=f"btn_reset_src_{account_key}"):
            st.session_state.pop(key, None)
            for k in ("creds_src","step3_done","src_account_label"):
                st.session_state.pop(k, None)
            clear_qp(); st.rerun()
    return creds

# --- Selección de cuenta SC (sin duplicar pregunta luego)
st.subheader("Selecciona la cuenta con acceso a Search Console")
account_options = ["Acceso", "Acceso Medios", "Acceso en cuenta personal de Nomadic"]
_default_label = st.session_state.get("sc_account_choice", "Acceso en cuenta personal de Nomadic")
default_idx = account_options.index(_default_label) if _default_label in account_options else 2

sc_choice = st.selectbox(
    "Elegí la cuenta para consultar datos de Search Console",
    account_options, index=default_idx, key="sc_account_choice"
)

def _choice_to_key(label: str) -> str | None:
    if norm(label) == norm("Acceso"): return "ACCESO"
    if norm(label) == norm("Acceso Medios"): return "ACCESO_MEDIOS"
    return None  # personal usa creds_dest

# Inicializar sc_service según selección
sc_service = None
if sc_choice == "Acceso en cuenta personal de Nomadic":
    creds_dest_dict = st.session_state.get("creds_dest") or token_store.load("creds_dest")
    if not creds_dest_dict:
        st.error("No encuentro la sesión personal. Volvé a iniciar sesión en el Paso 0."); st.stop()
    if not has_gsc_scope(creds_dest_dict.get("scopes")):
        st.warning("Tu cuenta personal no tiene permisos de Search Console todavía.")
        st.caption("Volvé a realizar el Paso 0 solicitando también el permiso de Search Console.")
        st.stop()
    try:
        creds_src = Credentials(**creds_dest_dict)
        sc_service = ensure_sc_client(creds_src)
        st.session_state["creds_src"] = creds_dest_dict
        st.session_state["src_account_label"] = "Acceso en cuenta personal de Nomadic"
        st.session_state["step3_done"] = True
        st.markdown(
            '''
            <div class="success-inline">
                Cuenta de acceso (Search Console): <strong>Acceso en cuenta personal de Nomadic</strong>
                <a href="?action=change_src" target="_self" rel="nofollow">(Cambiar cuenta de acceso)</a>
            </div>
            ''',
            unsafe_allow_html=True
        )
    except Exception as e:
        st.error(f"No pude inicializar Search Console con la cuenta personal: {e}")
        st.stop()
else:
    wanted_key = _choice_to_key(sc_choice)  # "ACCESO" o "ACCESO_MEDIOS"
    need_new_auth = (
        not st.session_state.get("step3_done") or
        norm(st.session_state.get("src_account_label")) != norm(sc_choice) or
        norm(st.session_state.get("src_account_label")) == norm("Acceso en cuenta personal de Nomadic")
    )
    if need_new_auth:
        creds_src_obj = pick_source_oauth_forced(wanted_key)
        if not creds_src_obj:
            st.stop()
        st.session_state["creds_src"] = {
            "token": creds_src_obj.token,
            "refresh_token": getattr(creds_src_obj, "refresh_token", None),
            "token_uri": creds_src_obj.token_uri,
            "client_id": creds_src_obj.client_id,
            "client_secret": creds_src_obj.client_secret,
            "scopes": list(getattr(creds_src_obj, "scopes", [])),
        }
        # Guardá también la fuente por si cambiás de pestaña
        token_store.save("creds_src", st.session_state["creds_src"])
        st.session_state["src_account_label"] = sc_choice
        st.session_state["step3_done"] = True
        clear_qp(); st.rerun()
    else:
        try:
            # Rehidratar si fuese necesario
            if not st.session_state.get("creds_src"):
                cdict = token_store.load("creds_src")
                if cdict:
                    st.session_state["creds_src"] = cdict
            creds_src = Credentials(**st.session_state["creds_src"])
            sc_service = ensure_sc_client(creds_src)
            src_label = st.session_state.get("src_account_label") or sc_choice
            st.markdown(
                f'''
                <div class="success-inline">
                    Cuenta de acceso (Search Console): <strong>{src_label}</strong>
                    <a href="{APP_HOME}?action=change_src" target="_self" rel="nofollow">(Cambiar cuenta de acceso)</a>
                </div>
                ''',
                unsafe_allow_html=True
            )
        except Exception as e:
            st.error(f"No pude inicializar el cliente de Search Console: {e}")
            st.stop()

# --- PASO: elegir sitio ---
def pick_site(sc_service):
    st.subheader("Elige el sitio a analizar")
    try:
        site_list = sc_service.sites().list().execute()
        sites = site_list.get("siteEntry", [])
    except Exception as e:
        st.error(f"Error al obtener sitios: {e}")
        st.stop()
    verified = [s for s in sites if s.get("permissionLevel") != "siteUnverifiedUser"]
    if not verified:
        st.error("No se encontraron sitios verificados en esta cuenta."); st.stop()
    options = sorted({s["siteUrl"] for s in verified})
    prev = st.session_state.get("site_url_choice")
    index = options.index(prev) if prev in options else 0
    site_url = st.selectbox("Sitio verificado:", options, index=index, key="site_url_choice")
    return site_url

site_url = pick_site(sc_service)

# ============== Flujos por análisis (requieren GSC) ==============
if analisis == "4":
    if run_core_update is None:
        st.warning("Este despliegue no incluye run_core_update.")
    else:
        params = params_for_core_update()
        if st.button("🚀 Ejecutar análisis de Core Update", type="primary"):
            adv_payload = st.session_state.get("core_filters_payload")
            if adv_payload:
                os.environ["SEO_ADVANCED_FILTERS"] = json.dumps(adv_payload, ensure_ascii=False)
            else:
                os.environ.pop("SEO_ADVANCED_FILTERS", None)
            sid = run_with_indicator(
                "Procesando Core Update",
                run_core_update, sc_service, drive_service, gs_client, site_url, params,
                st.session_state.get("dest_folder_id")
            )
            maybe_prefix_sheet_name_with_medio(drive_service, sid, site_url)
            st.success("¡Listo! Tu documento está creado.")
            st.markdown(f"➡️ **Abrir Google Sheets**: https://docs.google.com/spreadsheets/d/{sid}")
            with st.expander("Compartir acceso al documento (opcional)"):
                share_controls(drive_service, sid, default_email=_me.get("emailAddress") if _me else None)
            try:
                meta = drive_service.files().get(fileId=sid, fields="name,webViewLink").execute()
                sheet_name = meta.get("name", ""); sheet_url = meta.get("webViewLink") or f"https://docs.google.com/spreadsheets/d/{sid}"
            except Exception:
                sheet_name = ""; sheet_url = f"https://docs.google.com/spreadsheets/d/{sid}"
            activity_log_append(
                drive_service, gs_client,
                user_email=( _me or {}).get("emailAddress") or "",
                event="analysis", site_url=site_url,
                analysis_kind="Core Update",
                sheet_id=sid, sheet_name=sheet_name, sheet_url=sheet_url,
                gsc_account=st.session_state.get("src_account_label") or "",
                notes=f"params={params!r}"
            )
            st.session_state["last_file_id"] = sid
            st.session_state["last_file_kind"] = "core"

elif analisis == "5":
    if run_evergreen is None:
        st.warning("Este despliegue no incluye run_evergreen.")
    else:
        params = params_for_evergreen()
        if st.button("🌲 Ejecutar análisis Evergreen", type="primary"):
            sid = run_with_indicator(
                "Procesando Evergreen",
                run_evergreen, sc_service, drive_service, gs_client, site_url, params,
                st.session_state.get("dest_folder_id")
            )
            maybe_prefix_sheet_name_with_medio(drive_service, sid, site_url)
            st.success("¡Listo! Tu documento está creado.")
            st.markdown(f"➡️ **Abrir Google Sheets**: https://docs.google.com/spreadsheets/d/{sid}")
            with st.expander("Compartir acceso al documento (opcional)"):
                share_controls(drive_service, sid, default_email=_me.get("emailAddress") if _me else None)
            try:
                meta = drive_service.files().get(fileId=sid, fields="name,webViewLink").execute()
                sheet_name = meta.get("name", ""); sheet_url = meta.get("webViewLink") or f"https://docs.google.com/spreadsheets/d/{sid}"
            except Exception:
                sheet_name = ""; sheet_url = f"https://docs.google.com/spreadsheets/d/{sid}"
            activity_log_append(
                drive_service, gs_client,
                user_email=( _me or {}).get("emailAddress") or "",
                event="analysis", site_url=site_url,
                analysis_kind="Evergreen",
                sheet_id=sid, sheet_name=sheet_name, sheet_url=sheet_url,
                gsc_account=st.session_state.get("src_account_label") or "",
                notes=f"params={params!r}"
            )
            st.session_state["last_file_id"] = sid
            st.session_state["last_file_kind"] = "evergreen"

elif analisis == "6":
    if run_traffic_audit is None:
        st.warning("Este despliegue no incluye run_traffic_audit.")
    else:
        params = params_for_auditoria()
        if st.button("🧮 Ejecutar Auditoría de tráfico", type="primary"):
            sid = run_with_indicator(
                "Procesando Auditoría de tráfico",
                run_traffic_audit, sc_service, drive_service, gs_client, site_url, params,
                st.session_state.get("dest_folder_id")
            )
            maybe_prefix_sheet_name_with_medio(drive_service, sid, site_url)
            st.success("¡Listo! Tu documento está creado.")
            st.markdown(f"➡️ **Abrir Google Sheets**: https://docs.google.com/spreadsheets/d/{sid}")
            with st.expander("Compartir acceso al documento (opcional)"):
                share_controls(drive_service, sid, default_email=_me.get("emailAddress") if _me else None)
            try:
                meta = drive_service.files().get(fileId=sid, fields="name,webViewLink").execute()
                sheet_name = meta.get("name", ""); sheet_url = meta.get("webViewLink") or f"https://docs.google.com/spreadsheets/d/{sid}"
            except Exception:
                sheet_name = ""; sheet_url = f"https://docs.google.com/spreadsheets/d/{sid}"
            activity_log_append(
                drive_service, gs_client,
                user_email=( _me or {}).get("emailAddress") or "",
                event="analysis", site_url=site_url,
                analysis_kind="Auditoría",
                sheet_id=sid, sheet_name=sheet_name, sheet_url=sheet_url,
                gsc_account=st.session_state.get("src_account_label") or "",
                notes=f"params={params!r}"
            )
            st.session_state["last_file_id"] = sid
            st.session_state["last_file_kind"] = "audit"

elif analisis == "9":
    # ===== NUEVO: Análisis de contenido =====
    if (run_content_analysis is None) or (params_for_content is None):
        st.warning("Este despliegue no incluye `run_content_analysis` y/o `params_for_content` (repo externo). "
                   "Actualizá el paquete `seo_analisis_ext` para habilitarlo.")
    else:
        st.subheader("Configuración del Análisis de contenido")

        # 1) Render del formulario
        params = params_for_content()

        # 2) Resolver SIEMPRE la ventana y normalizar sinónimos
        try:
            w = params.get("window") or {}
            lag_days = int(params.get("lag_days", 0))
            mode = (w.get("mode") or "last").lower()
            days = int(w.get("days") or 28)

            start_date = w.get("start_date")
            end_date = w.get("end_date")

            if isinstance(start_date, str):
                start_date = pd.to_datetime(start_date).date()
            if isinstance(end_date, str):
                end_date = pd.to_datetime(end_date).date()

            if (mode != "custom") or (not start_date or not end_date):
                today_ok = _date.today() - _td(days=lag_days)
                end_date = today_ok
                start_date = end_date - _td(days=days - 1)

            # Grabar resueltos y alias
            params.setdefault("window", {})
            params["window"]["start_date"] = start_date
            params["window"]["end_date"] = end_date
            params["window"]["resolved_start"] = start_date
            params["window"]["resolved_end"] = end_date
            params["window"]["mode"] = "custom"
            params["window"]["days"] = int((end_date - start_date).days) + 1
            params["window"]["start"] = start_date
            params["window"]["end"] = end_date
            params["window"]["from"] = start_date
            params["window"]["to"] = end_date
        except Exception:
            today_ok = _date.today() - _td(days=int(params.get("lag_days", 0)))
            start_date = today_ok - _td(days=27)
            end_date = today_ok
            params.setdefault("window", {})
            params["window"]["start_date"] = start_date
            params["window"]["end_date"] = end_date
            params["window"]["resolved_start"] = start_date
            params["window"]["resolved_end"] = end_date
            params["window"]["mode"] = "custom"
            params["window"]["days"] = 28
            params["window"]["start"] = start_date
            params["window"]["end"] = end_date
            params["window"]["from"] = start_date
            params["window"]["to"] = end_date

        # Sinónimos source/tipo & order
        src_label = (params.get("tipo") or params.get("source") or "Ambos")
        src_map = {"Search":"search","Discover":"discover","Ambos":"both","Search + Discover":"both"}
        params["source"] = src_map.get(src_label, "both")
        params["tipo"] = {"search":"Search","discover":"Discover","both":"Ambos"}[params["source"]]
        if "order" not in params and "order_by" in params:
            params["order"] = params["order_by"]

        # 3) Preflight GSC (para diagnosticar por qué sale vacío)
        def _gsc_preflight(sc, site_url, start_d, end_d, kind, limit=20, country=None, device=None):
            # kind: 'search' | 'discover'
            api_type = "discover" if kind == "discover" else "web"
            body = {
                "startDate": str(start_d),
                "endDate": str(end_d),
                "dimensions": ["page"],
                "rowLimit": int(limit),
                "type": api_type
            }
            # Filtros opcionales simples (defensivos)
            if country:
                try:
                    body.setdefault("dimensionFilterGroups", []).append({
                        "filters": [{
                            "dimension": "country", "operator": "equals", "expression": country
                        }]
                    })
                except Exception:
                    pass
            if device:
                try:
                    dev_map = {"desktop":"DESKTOP","mobile":"MOBILE","tablet":"TABLET"}
                    body.setdefault("dimensionFilterGroups", []).append({
                        "filters": [{
                            "dimension": "device", "operator": "equals", "expression": dev_map.get(device.lower(), device.upper())
                        }]
                    })
                except Exception:
                    pass
            try:
                resp = sc.searchanalytics().query(siteUrl=site_url, body=body).execute()
                rows = resp.get("rows", [])
                pages = [r.get("keys", [""])[0] for r in rows if r.get("keys")]
                return {"ok": True, "count": len(rows), "sample": pages[:10], "body": body}
            except Exception as e:
                return {"ok": False, "error": str(e), "body": body}

        country = (params.get("filters") or {}).get("country")
        device = (params.get("filters") or {}).get("device")

        with st.expander("🔎 Preflight de datos GSC (previo a ejecutar)"):
            col1, col2 = st.columns(2)
            with col1:
                pr_search = _gsc_preflight(sc_service, site_url, params["window"]["start_date"], params["window"]["end_date"], "search", limit=20, country=country, device=device)
                st.markdown("**Search (web)**")
                if pr_search["ok"]:
                    st.write(f"Filas: {pr_search['count']}")
                    if pr_search["count"] == 0:
                        st.info("Search devolvió 0 filas para este rango/filtros.")
                    else:
                        st.caption("Muestra de páginas:")
                        st.write(pr_search["sample"])
                else:
                    st.warning("Error al consultar Search.")
                    st.code(pr_search["error"])
            with col2:
                pr_disc = _gsc_preflight(sc_service, site_url, params["window"]["start_date"], params["window"]["end_date"], "discover", limit=20, country=country, device=device)
                st.markdown("**Discover**")
                if pr_disc["ok"]:
                    st.write(f"Filas: {pr_disc['count']}")
                    if pr_disc["count"] == 0:
                        st.info("Discover devolvió 0 filas para este rango/filtros.")
                    else:
                        st.caption("Muestra de páginas:")
                        st.write(pr_disc["sample"])
                else:
                    st.warning("Error al consultar Discover.")
                    st.code(pr_disc["error"])

        # 4) Previa: mostrar SIEMPRE el payload que se enviará
        with st.expander("🧾 Ver payload que se enviará (previa)", expanded=False):
            st.code(json.dumps(params, ensure_ascii=False, indent=2, default=str))

        # 5) Validaciones para habilitar botón
        selectors = params.get("selectors") or params.get("scrape", {}).get("selectors") or {}
        selectors_ok = isinstance(selectors, dict) and len(selectors) > 0
        can_run = selectors_ok  # fechas ya resueltas arriba

        if not selectors_ok:
            st.warning("Definí **selectores válidos** en la sección de selectores.")

        if st.button("📰 Ejecutar Análisis de contenido", type="primary", disabled=not can_run, key="btn_run_content"):
            st.session_state["content_last_params"] = params
            try:
                with st.spinner("Procesando Análisis de contenido..."):
                    sid = run_content_analysis(
                        sc_service, drive_service, gs_client, site_url,
                        params,  # usar EXACTAMENTE lo que se acaba de configurar
                        st.session_state.get("dest_folder_id")
                    )
            except Exception as e:
                sid = None
                st.session_state["content_last_error"] = str(e)

            if not sid:
                st.error("No se generó el documento. Abajo dejo el detalle del error y el payload enviado.")
                with st.expander("Ver detalle técnico"):
                    err = (
                        st.session_state.get("_rca_error")
                        or st.session_state.get("content_last_error")
                        or "(sin mensaje)"
                    )
                    st.write(err)
                    norm_params = st.session_state.get("_rca_norm_params") or st.session_state.get("content_last_params") or params
                    st.code(json.dumps(norm_params, ensure_ascii=False, indent=2, default=str))
            else:
                # Renombrar el archivo con fechas resueltas para evitar "none a none"
                from urllib.parse import urlparse as _urlparse
                host = _urlparse(site_url).netloc or site_url
                new_name = f"Análisis de contenido — {host} — {params['window']['start_date']} a {params['window']['end_date']}"
                try:
                    drive_service.files().update(fileId=sid, body={"name": new_name}).execute()
                except Exception:
                    pass

                # Prefijo con medio si corresponde
                try:
                    maybe_prefix_sheet_name_with_medio(drive_service, sid, site_url)
                except Exception:
                    pass

                st.success("¡Listo! Tu documento está creado.")
                st.markdown(f"➡️ **Abrir Google Sheets**: https://docs.google.com/spreadsheets/d/{sid}")

                # Mostrar SIEMPRE el payload utilizado (exitoso)
                with st.expander("🧾 Ver payload utilizado (siempre)"):
                    used_params = st.session_state.get("_rca_norm_params") or st.session_state.get("content_last_params") or params
                    st.code(json.dumps(used_params, ensure_ascii=False, indent=2, default=str))

                with st.expander("Compartir acceso al documento (opcional)"):
                    share_controls(drive_service, sid, default_email=_me.get("emailAddress") if _me else None)
                try:
                    meta = drive_service.files().get(fileId=sid, fields="name,webViewLink").execute()
                    sheet_name = meta.get("name", ""); sheet_url = meta.get("webViewLink") or f"https://docs.google.com/spreadsheets/d/{sid}"
                except Exception:
                    sheet_name = ""; sheet_url = f"https://docs.google.com/spreadsheets/d/{sid}"
                activity_log_append(
                    drive_service, gs_client,
                    user_email=( _me or {}).get("emailAddress") or "",
                    event="analysis", site_url=site_url,
                    analysis_kind="Análisis de contenido",
                    sheet_id=sid, sheet_name=sheet_name, sheet_url=sheet_url,
                    gsc_account=st.session_state.get("src_account_label") or "",
                    notes="ok"
                )
                st.session_state["last_file_id"] = sid
                st.session_state["last_file_kind"] = "content"

else:
    st.info("Las opciones 1, 2 y 3 aún no están disponibles en esta versión.")

# --- Panel persistente de resumen (una sola vez) ---
if st.session_state.get("last_file_id") and st.session_state.get("last_file_kind"):
    st.divider()
    st.subheader("📄 Resumen del análisis")
    st.caption("Podés generar o regenerar el resumen sin volver a ejecutar el análisis.")
    gemini_summary(
        gs_client,
        st.session_state["last_file_id"],
        kind=st.session_state["last_file_kind"],
        force_prompt_key="core" if st.session_state["last_file_kind"] == "core" else None,
        widget_suffix="panel"
    )

if st.session_state.get("DEBUG"):
    st.write(
        "¿Gemini listo?",
        "GEMINI_API_KEY" in st.secrets or ("gemini" in st.secrets and "api_key" in st.secrets.get('gemini', {}))
    )