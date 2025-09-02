from __future__ import annotations

# --- Permisos OAuth en localhost + tolerancia de scope
import os
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

import sys
import json
import asyncio
from types import SimpleNamespace
from datetime import date, timedelta

import pandas as pd
import streamlit as st
from google.oauth2.credentials import Credentials

# ====== Config base ======
try:
    st.set_page_config(layout="wide", page_title="Análisis SEO", page_icon="📊")
except Exception:
    pass

# ---- Shims de compatibilidad (por si estos módulos están fuera)
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

# ⬇️ Sin espacios arriba + CSS
st.markdown("""
<style>
#nmd-band, .nmd-band, [data-nmd="band"], [id*="band"], [class*="band"] {
  display: none !important; height:0 !important; margin:0 !important; padding:0 !important;
}
div[data-testid="stAppViewContainer"] { padding-top: 0 !important; }
main .block-container { margin-top: 0 !important; padding-top: .75rem !important; }
header[data-testid="stHeader"] { z-index: 1500 !important; }
/* Sidebar abierto */
:root:has([data-testid="stSidebar"][aria-expanded="true"])
  header[data-testid="stHeader"]::before { left: 350px !important; }
/* Sidebar cerrado */
:root:has([data-testid="stSidebar"][aria-expanded="false"])
  header[data-testid="stHeader"]::before { left: 100px !important; }
/* Fallback */
:root:not(:has([data-testid="stSidebar"])) header[data-testid="stHeader"]::before { left: 16px !important; }
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
        opciones.append("9. Análisis de contenido (repo externo) ✅")
    # EXTRACTOR rápido
    opciones.append("10. Extractor rápido GSC → H1 (+ metadatos) ✅")

    key = st.radio("Tipos disponibles:", opciones, index=len(opciones)-1, key="analysis_choice")
    if key.startswith("4."): return "4"
    if key.startswith("5."): return "5"
    if key.startswith("6."): return "6"
    if key.startswith("7."): return "7"
    if key.startswith("8."): return "8"
    if key.startswith("9."): return "9"
    if key.startswith("10."): return "10"
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
            if st.button("🔎 Ejecutar Análisis de Nombres (KG + Wikipedia)", type="primary", key="btn_names_run"):
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
                    user_email=( _me or {}).get("emailAddress") or "",
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
        up = st.file_uploader("Archivo CSV", type=["csv"], key="ds_file")
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
            if st.button("🔎 Ejecutar Análisis Discover Snoop", type="primary", key="btn_ds_run"):
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
                    user_email=( _me or {}).get("emailAddress") or "",
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
        token_store.save("creds_src", st.session_state["creds_src"])
        st.session_state["src_account_label"] = sc_choice
        st.session_state["step3_done"] = True
        clear_qp(); st.rerun()
    else:
        try:
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

# =========================
# Utilidades comunes
# =========================

def _iso3_lower(x: str | None) -> str | None:
    if not x: return None
    return str(x).strip().lower()

def _device_upper(x: str | None) -> str | None:
    if not x: return None
    v = str(x).strip().lower()
    if v in ("desktop","mobile","tablet"):
        return v.upper()
    return None

def _gsc_fetch_top_urls(sc, site: str, start: date, end: date, search_type: str,
                        country: str | None, device: str | None,
                        order_by: str, row_limit: int) -> list[dict]:
    """
    search_type: "web" (Search) | "discover"
    order_by: "clicks" | "impressions" | "ctr" | "position"
    """
    try:
        body = {
            "startDate": str(start),
            "endDate": str(end),
            "dimensions": ["page"],
            "rowLimit": int(row_limit),
            "startRow": 0,
            "type": search_type,
            "orderBy": [{"field": order_by, "descending": True}],
        }
        filters = []
        if country:
            filters.append({
                "dimension": "country",
                "operator": "equals",
                "expression": _iso3_lower(country)
            })
        if device:
            filters.append({
                "dimension": "device",
                "operator": "equals",
                "expression": _device_upper(device)
            })
        if filters:
            body["dimensionFilterGroups"] = [{"groupType":"and","filters":filters}]
        resp = sc.searchanalytics().query(siteUrl=site, body=body).execute()
        rows = resp.get("rows", []) or []
        out = []
        for r in rows:
            keys = r.get("keys") or []
            page = keys[0] if keys else ""
            out.append({
                "page": page,
                "clicks": r.get("clicks", 0),
                "impressions": r.get("impressions", 0),
                "ctr": r.get("ctr", 0.0),
                "position": r.get("position", 0.0),
            })
        return out
    except Exception as e:
        st.session_state["_fast_error"] = f"GSC query error ({search_type}): {e}"
        return []

_DROP_PATTERNS = (
    "/player/", "/tag/", "/tags/", "/etiqueta/", "/categoria/", "/category/",
    "/author/", "/autores/", "/programas/", "/hd/", "/podcast", "/videos/",
    "/video/", "/envivo", "/en-vivo", "/en_vivo", "/live", "/player-", "?"
)
def _is_article_url(u: str) -> bool:
    if not u: return False
    u = u.strip().lower()
    if u in ("https://", "http://"): return False
    if u.endswith((".jpg",".jpeg",".png",".gif",".svg",".webp",".mp4",".mp3",".m3u8",".pdf",".webm",".avi",".mov")):
        return False
    if u.count("/") <= 3:
        return False
    for p in _DROP_PATTERNS:
        if p in u:
            return False
    return True

def _filter_article_urls(urls: list[str]) -> list[str]:
    return [u for u in urls if _is_article_url(u)]

def _suggest_user_agent(ua: str | None) -> str:
    if ua and ua.strip():
        return ua
    return ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36")

# -------------------------
# Scraping rápido (async) + fallback sync
# -------------------------
def _parse_html_for_meta(html: str, wants: dict, xpaths: dict, joiner: str = " | ") -> dict:
    """
    Extrae campos en función de 'wants' (dict de booleans) y 'xpaths' (opcional).
    Campos soportados:
      h1, title, meta_description, og_title, og_description, canonical, published_time, lang,
      first_paragraph,
      h2_list, h2_count, h3_list, h3_count,
      bold_count, bold_list,
      link_count, link_anchor_texts,
      related_links_count, related_link_anchors,
      tags_list
    *IMPORTANTE*: h2/h3/bold/link(s) se buscan SOLO dentro del contenedor del artículo si se provee
    `xpaths['article']`. Si no se provee, se usa heurística (//article | //main).
    """
    data = {
        "h1": "", "title": "", "meta_description": "", "og_title": "", "og_description": "",
        "canonical": "", "published_time": "", "lang": "",
        "first_paragraph": "",
        "h2_list": "", "h2_count": 0, "h3_list": "", "h3_count": 0,
        "bold_count": 0, "bold_list": "",
        "link_count": 0, "link_anchor_texts": "",
        "related_links_count": 0, "related_link_anchors": "",
        "tags_list": ""
    }

    # Intentar lxml para XPath
    doc = None
    have_lxml = False
    try:
        import lxml.html as LH  # type: ignore
        doc = LH.fromstring(html)
        have_lxml = True
    except Exception:
        have_lxml = False

    # BeautifulSoup para heurísticas
    soup = None
    try:
        from bs4 import BeautifulSoup  # type: ignore
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            soup = BeautifulSoup(html, "html.parser")
    except Exception:
        soup = None

    def _meta_bs(name=None, prop=None):
        if not soup: return ""
        if name:
            el = soup.find("meta", attrs={"name": name})
            if el: return (el.get("content") or "").strip()
        if prop:
            el = soup.find("meta", attrs={"property": prop})
            if el: return (el.get("content") or "").strip()
        return ""

    def _xpath_text_list(_doc_or_node, xp: str) -> list[str]:
        if not _doc_or_node or not xp: return []
        try:
            nodes = _doc_or_node.xpath(xp)
            out = []
            for n in nodes:
                if isinstance(n, str):
                    txt = n.strip()
                elif hasattr(n, "text_content"):
                    txt = n.text_content().strip()
                else:
                    txt = str(n).strip()
                if txt:
                    out.append(txt)
            return out
        except Exception:
            return []

    # Determinar contenedor del artículo (scope) para h2/h3/bold/links (y primer párrafo)
    lxml_scope_nodes = []
    soup_scope = None
    xp_article = (xpaths.get("article") or "").strip()
    if have_lxml:
        try:
            if xp_article:
                nodes = doc.xpath(xp_article)
                lxml_scope_nodes = [n for n in nodes if hasattr(n, "xpath")]
            if not lxml_scope_nodes:
                lxml_scope_nodes = [n for n in doc.xpath("//article | //main") if hasattr(n, "xpath")]
        except Exception:
            lxml_scope_nodes = []
    if soup and not lxml_scope_nodes:
        try:
            soup_scope = soup.select_one("article") or soup.select_one("main")
        except Exception:
            soup_scope = None

    # --- Campos básicos (document-wide) ---
    if wants.get("title"):
        if soup and soup.title and soup.title.string:
            data["title"] = soup.title.string.strip()
        elif have_lxml:
            try:
                t = doc.xpath("string(//title)")
                data["title"] = (t or "").strip()
            except Exception:
                pass

    if wants.get("h1"):
        if have_lxml:
            try:
                t = doc.xpath("string((//h1)[1])")
                data["h1"] = (t or "").strip()
            except Exception:
                pass
        if not data["h1"] and soup:
            el = soup.find("h1")
            if el: data["h1"] = el.get_text(strip=True)

    if wants.get("meta_description"):
        data["meta_description"] = _meta_bs(name="description") or _meta_bs(prop="description")

    if wants.get("og_title"):
        data["og_title"] = _meta_bs(prop="og:title")

    if wants.get("og_description"):
        data["og_description"] = _meta_bs(prop="og:description")

    if wants.get("canonical"):
        if have_lxml:
            try:
                hrefs = doc.xpath("//link[translate(@rel,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')='canonical']/@href")
                if hrefs: data["canonical"] = hrefs[0].strip()
            except Exception:
                pass
        if not data["canonical"] and soup:
            try:
                link = soup.find("link", rel=lambda v: v and ("canonical" in [x.lower() for x in (v if isinstance(v, list) else [v])]))
                if link: data["canonical"] = (link.get("href") or "").strip()
            except Exception:
                pass

    if wants.get("published_time"):
        val = _meta_bs(prop="article:published_time") or _meta_bs(name="pubdate") or _meta_bs(name="date")
        if not val and have_lxml:
            try:
                val = (doc.xpath("string(//time/@datetime)")) or (doc.xpath("string(//time[1])"))
            except Exception:
                pass
        if not val and soup:
            try:
                time_tag = soup.find("time")
                if time_tag:
                    val = (time_tag.get("datetime") or "").strip() or time_tag.get_text(strip=True)
            except Exception:
                pass
        data["published_time"] = (val or "").strip()

    if wants.get("lang"):
        if have_lxml:
            try:
                data["lang"] = (doc.xpath("string(//html/@lang)") or "").strip()
            except Exception:
                pass
        if not data["lang"] and soup:
            try:
                html_tag = soup.find("html")
                if html_tag:
                    data["lang"] = (html_tag.get("lang") or "").strip()
            except Exception:
                pass

    # --- Avanzados (dentro del artículo cuando aplique) ---
    # Primer párrafo
    if wants.get("first_paragraph"):
        xp_first = (xpaths.get("first_paragraph") or "").strip()
        text = ""
        if xp_first and have_lxml:
            lst = _xpath_text_list(doc, xp_first)
            text = next((t for t in lst if t.strip()), "")
        if not text:
            if have_lxml and lxml_scope_nodes:
                for node in lxml_scope_nodes:
                    try:
                        t = node.xpath("string(.//p[normalize-space()][1])")
                        if t and t.strip():
                            text = t.strip(); break
                    except Exception:
                        pass
            if not text and soup_scope:
                p = soup_scope.find("p")
                if p: text = p.get_text(strip=True)
            if not text and soup:
                p = soup.find("p")
                if p: text = p.get_text(strip=True)
        data["first_paragraph"] = text

    # Helper para juntar textos dentro del scope lxml
    def _collect_scope_texts(nodeset, xpath_rel: str) -> list[str]:
        vals: list[str] = []
        if nodeset:
            for node in nodeset:
                try:
                    parts = node.xpath(xpath_rel)
                except Exception:
                    parts = []
                for p in parts:
                    if isinstance(p, str):
                        txt = p.strip()
                    elif hasattr(p, "text_content"):
                        txt = p.text_content().strip()
                    else:
                        txt = str(p).strip()
                    if txt:
                        vals.append(txt)
        return vals

    # H2
    if wants.get("h2_list") or wants.get("h2_count"):
        xp_h2 = (xpaths.get("h2") or "").strip()
        h2s: list[str] = []
        if xp_h2 and have_lxml:
            if lxml_scope_nodes and (xp_h2.startswith(".") or not xp_h2.startswith("/")):
                h2s = _collect_scope_texts(lxml_scope_nodes, xp_h2 if xp_h2.startswith(".") else ".//" + xp_h2.strip("./"))
            else:
                h2s = _xpath_text_list(doc, xp_h2)
        elif have_lxml and lxml_scope_nodes:
            h2s = _collect_scope_texts(lxml_scope_nodes, ".//h2")
        elif soup_scope:
            h2s = [el.get_text(strip=True) for el in soup_scope.find_all("h2")]
        h2s = [t for t in (h2s or []) if t]
        if wants.get("h2_list"):  data["h2_list"]  = (joiner.join(h2s)) if h2s else ""
        if wants.get("h2_count"): data["h2_count"] = len(h2s)

    # H3
    if wants.get("h3_list") or wants.get("h3_count"):
        xp_h3 = (xpaths.get("h3") or "").strip()
        h3s: list[str] = []
        if xp_h3 and have_lxml:
            if lxml_scope_nodes and (xp_h3.startswith(".") or not xp_h3.startswith("/")):
                h3s = _collect_scope_texts(lxml_scope_nodes, xp_h3 if xp_h3.startswith(".") else ".//" + xp_h3.strip("./"))
            else:
                h3s = _xpath_text_list(doc, xp_h3)
        elif have_lxml and lxml_scope_nodes:
            h3s = _collect_scope_texts(lxml_scope_nodes, ".//h3")
        elif soup_scope:
            h3s = [el.get_text(strip=True) for el in soup_scope.find_all("h3")]
        h3s = [t for t in (h3s or []) if t]
        if wants.get("h3_list"):  data["h3_list"]  = (joiner.join(h3s)) if h3s else ""
        if wants.get("h3_count"): data["h3_count"] = len(h3s)

    # Negritas — count + lista (SOLO dentro del artículo)
    if wants.get("bold_count") or wants.get("bold_list"):
        cnt = 0
        blist: list[str] = []
        if have_lxml and lxml_scope_nodes:
            for node in lxml_scope_nodes:
                try:
                    bs = node.xpath(".//*[self::b or self::strong]")
                    cnt += len(bs)
                    if wants.get("bold_list"):
                        for b in bs:
                            try:
                                t = b.text_content().strip()
                                if t: blist.append(t)
                            except Exception:
                                pass
                except Exception:
                    pass
        elif soup_scope:
            try:
                bs = soup_scope.select("b, strong")
                cnt = len(bs)
                if wants.get("bold_list"):
                    blist = [el.get_text(strip=True) for el in bs if el.get_text(strip=True)]
            except Exception:
                cnt = 0
        data["bold_count"] = int(cnt or 0)
        if wants.get("bold_list"):
            data["bold_list"] = joiner.join([t for t in blist if t])

    # Links — count + anchors (SOLO dentro del artículo)
    if wants.get("link_count") or wants.get("link_anchor_texts"):
        cnt = 0
        anchors: list[str] = []
        if have_lxml and lxml_scope_nodes:
            for node in lxml_scope_nodes:
                try:
                    alist = node.xpath(".//a[@href]")
                    cnt += len(alist)
                    if wants.get("link_anchor_texts"):
                        for a in alist:
                            try:
                                t = a.text_content().strip()
                                if t: anchors.append(t)
                            except Exception:
                                pass
                except Exception:
                    pass
        elif soup_scope:
            try:
                alist = soup_scope.find_all("a", href=True)
                cnt = len(alist)
                if wants.get("link_anchor_texts"):
                    anchors = [a.get_text(strip=True) for a in alist if a.get_text(strip=True)]
            except Exception:
                cnt = 0
        data["link_count"] = int(cnt or 0)
        if wants.get("link_anchor_texts"):
            data["link_anchor_texts"] = joiner.join([t for t in anchors if t])

    # Caja de noticias relacionadas (xpath al contenedor) → count + anchors
    if wants.get("related_links_count") or wants.get("related_link_anchors"):
        xp_rel = (xpaths.get("related_box") or "").strip()
        rel_cnt = 0
        rel_anchors: list[str] = []
        if xp_rel and have_lxml:
            try:
                boxes = doc.xpath(xp_rel)
            except Exception:
                boxes = []
            for bx in boxes:
                try:
                    alist = bx.xpath(".//a[@href]")
                except Exception:
                    alist = []
                rel_cnt += len(alist)
                if wants.get("related_link_anchors"):
                    for a in alist:
                        try:
                            t = a.text_content().strip()
                            if t: rel_anchors.append(t)
                        except Exception:
                            pass
        data["related_links_count"] = int(rel_cnt or 0)
        if wants.get("related_link_anchors"):
            data["related_link_anchors"] = joiner.join([t for t in rel_anchors if t])

    # Tags (lista)
    if wants.get("tags_list"):
        xp_tags = (xpaths.get("tags") or "").strip()
        tags = []
        if xp_tags and have_lxml:
            if lxml_scope_nodes and (xp_tags.startswith(".") or not xp_tags.startswith("/")):
                for node in lxml_scope_nodes:
                    tags += _xpath_text_list(node, xp_tags if xp_tags.startswith(".") else ".//" + xp_tags.strip("./"))
            else:
                tags = _xpath_text_list(doc, xp_tags)
        else:
            mt = []
            if have_lxml:
                try:
                    mt = [t for t in doc.xpath("//meta[@property='article:tag']/@content") if t and str(t).strip()]
                except Exception:
                    mt = []
            if not mt and soup:
                try:
                    mt = [ (m.get("content") or "").strip()
                           for m in soup.find_all("meta", attrs={"property":"article:tag"}) ]
                    mt = [t for t in mt if t]
                except Exception:
                    mt = []
            tags = mt
        tags = [t.strip() for t in (tags or []) if t and str(t).strip()]
        data["tags_list"] = (joiner.join(tags)) if tags else ""

    return data

async def _fetch_one(session, url: str, ua: str, timeout_s: int, wants: dict, xpaths: dict, joiner: str) -> dict:
    base = {"url": url, "ok": False, "status": 0, "error": ""}
    try:
        async with session.get(url, headers={"User-Agent": ua}, timeout=timeout_s, allow_redirects=True) as resp:
            base["status"] = resp.status
            if resp.status >= 400:
                base["error"] = f"http {resp.status}"
                return base
            html = await resp.text(errors="ignore")
            meta = _parse_html_for_meta(html, wants=wants, xpaths=xpaths, joiner=joiner)
            base.update(meta)
            base["ok"] = True
            return base
    except Exception as e:
        base["error"] = str(e)
        return base

async def _scrape_async(urls: list[str], ua: str, wants: dict, xpaths: dict, joiner: str,
                        timeout_s: int = 12, concurrency: int = 20) -> list[dict]:
    # aiohttp es opcional
    try:
        import aiohttp  # type: ignore
    except Exception:
        return _scrape_sync(urls, ua, wants, xpaths, joiner, timeout_s, concurrency)

    connector = aiohttp.TCPConnector(limit=concurrency, ssl=False)
    timeout = aiohttp.ClientTimeout(total=max(timeout_s+2, timeout_s))
    results: list[dict] = []
    sem = asyncio.Semaphore(concurrency)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout, trust_env=True) as session:
        async def _bound(u):
            async with sem:
                return await _fetch_one(session, u, ua, timeout_s, wants, xpaths, joiner)
        tasks = [_bound(u) for u in urls]
        done = 0
        progress = st.progress(0.0, text="Scrapeando páginas…")
        for coro in asyncio.as_completed(tasks):
            res = await coro
            results.append(res)
            done += 1
            progress.progress(done/len(tasks), text=f"Scrapeando páginas… {done}/{len(tasks)}")
        progress.empty()
    order = {u:i for i,u in enumerate(urls)}
    results.sort(key=lambda r: order.get(r.get("url",""), 1e9))
    return results

def _scrape_sync(urls: list[str], ua: str, wants: dict, xpaths: dict, joiner: str,
                 timeout_s: int = 12, concurrency: int = 12) -> list[dict]:
    try:
        import requests
    except Exception as e:
        return [{"url": u, "ok": False, "status": 0, "error": f"requests no disponible: {e}"} for u in urls]
    from concurrent.futures import ThreadPoolExecutor, as_completed
    results: list[dict] = []
    headers = {"User-Agent": ua}

    def _one(u: str) -> dict:
        base = {"url": u, "ok": False, "status": 0, "error": ""}
        try:
            rs = requests.get(u, headers=headers, timeout=timeout_s, allow_redirects=True)
            base["status"] = rs.status_code
            if rs.status_code >= 400:
                base["error"] = f"http {rs.status_code}"
                return base
            meta = _parse_html_for_meta(rs.text, wants=wants, xpaths=xpaths, joiner=joiner)
            base.update(meta)
            base["ok"] = True
        except Exception as e:
            base["error"] = str(e)
        return base

    progress = st.progress(0.0, text="Scrapeando páginas…")
    done = 0
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futs = [ex.submit(_one, u) for u in urls]
        for f in as_completed(futs):
            results.append(f.result())
            done += 1
            progress.progress(done/len(futs), text=f"Scrapeando páginas… {done}/{len(futs)}")
    progress.empty()
    order = {u:i for i,u in enumerate(urls)}
    results.sort(key=lambda r: order.get(r.get("url",""), 1e9))
    return results

# ============== Flujos por análisis (requieren GSC) ==============
if analisis == "4":
    if run_core_update is None:
        st.warning("Este despliegue no incluye run_core_update.")
    else:
        params = params_for_core_update()
        if st.button("🚀 Ejecutar análisis de Core Update", type="primary", key="btn_core_run"):
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
        if st.button("🌲 Ejecutar análisis Evergreen", type="primary", key="btn_ev_run"):
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
        if st.button("🧮 Ejecutar Auditoría de tráfico", type="primary", key="btn_aud_run"):
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
    # ===== NUEVO: Análisis de contenido (repo externo) =====
    if (run_content_analysis is None) or (params_for_content is None):
        st.warning("Este despliegue no incluye `run_content_analysis` y/o `params_for_content` (repo externo). "
                   "Actualizá el paquete `seo_analisis_ext` para habilitarlo.")
    else:
        st.info("Este modo utiliza el runner externo. Si preferís algo más simple/rápido, probá el **Extractor rápido** (opción 10).")
        st.warning("El modo 9 está disponible, pero se recomienda usar el 10 para H1+metadatos directos desde GSC.")

elif analisis == "10":
    # ===== EXTRACTOR RÁPIDO: GSC → URLs → H1 (+ metadatos) → Sheets =====
    st.subheader("Extractor rápido desde Search Console")
    st.caption("Trae URLs por Search / Discover (o ambos), filtra por país / dispositivo, scrapea rápido **solo los campos que elijas** (limitados al cuerpo del artículo si indicas su XPath) y publica en Sheets.")

    # Config de fechas
    colA, colB, colC = st.columns([1,1,2])
    with colA:
        end_default = date.today() - timedelta(days=2)
        end_date = st.date_input("Hasta (inclusive)", value=end_default, key="fast_end")
    with colB:
        days = st.number_input("Días (ventana)", min_value=1, max_value=90, value=28, step=1, key="fast_days")
        start_date = end_date - timedelta(days=int(days)-1)
        st.write(f"Desde: **{start_date}**")
    with colC:
        tipo = st.radio("Origen", ["Search", "Discover", "Ambos"], horizontal=True, key="fast_source")

    # Filtros país / dispositivo
    col1, col2, col3, col4 = st.columns([1,1,1,1])
    with col1:
        country = st.text_input("País (ISO-3166-1 alpha-3, ej: ARG, USA, ESP) — opcional", value="", key="fast_country").strip().upper()
    with col2:
        device = st.selectbox("Dispositivo (opcional)", ["", "DESKTOP", "MOBILE", "TABLET"], index=0, key="fast_device")
    with col3:
        order_by = st.selectbox("Ordenar por", ["clicks","impressions","ctr","position"], index=0, key="fast_order")
    with col4:
        row_limit = st.number_input("Máx URLs por origen", min_value=10, max_value=5000, value=500, step=10, key="fast_row_lim")

    # Umbrales
    col5, col6, col7 = st.columns([1,1,1])
    with col5:
        min_clicks = st.number_input("Min. clics", min_value=0, max_value=1000000, value=0, step=10, key="fast_min_clicks")
    with col6:
        min_impr = st.number_input("Min. impresiones", min_value=0, max_value=10000000, value=0, step=100, key="fast_min_impr")
    with col7:
        only_articles = st.checkbox("Solo artículos (filtra tags/player/etc.)", value=True, key="fast_only_articles")

    # Scraping setup
    st.markdown("### ⚙️ Campos a extraer")
    colX, colY = st.columns(2)

    with colX:
        w_title = st.checkbox("Title", value=True, key="w_title")
        w_h1 = st.checkbox("H1", value=True, key="w_h1")
        w_md = st.checkbox("Meta description", value=True, key="w_md")
        w_ogt = st.checkbox("OG:title", value=False, key="w_ogt")
        w_ogd = st.checkbox("OG:description", value=False, key="w_ogd")
        w_canon = st.checkbox("Canonical", value=True, key="w_canon")
        w_pub = st.checkbox("Fecha publicación (meta/time)", value=False, key="w_pub")
        w_lang = st.checkbox("Lang (html@lang)", value=False, key="w_lang")
        w_firstp = st.checkbox("Primer párrafo (XPath opcional)", value=True, key="w_firstp")
        xp_firstp = st.text_input("XPath Primer párrafo (opcional)", value="", key="xp_firstp",
                                  help="Ej: //article//p[normalize-space()][1]  |  relativo al contenedor si empieza con .//")

        # XPath del contenedor del artículo
        xp_article = st.text_input(
            "XPath del contenedor del artículo (recomendado)",
            value="",
            key="xp_article",
            help="Define el scope de h2/h3/negritas/links. Ej: //article | //main[@id='content'] | .//div[@data-type='article-body']"
        )
        st.caption("Si no lo indicás, usaré heurística (//article | //main).")

        # Caja de noticias relacionadas
        st.markdown("**Caja de noticias relacionadas**")
        w_rel_count = st.checkbox("Cantidad de links en caja de relacionadas", value=False, key="w_rel_count")
        w_rel_anchors = st.checkbox("Anchor text de relacionadas (lista)", value=False, key="w_rel_anchors")
        xp_related = st.text_input("XPath de la caja de relacionadas (contenedor)", value="", key="xp_related",
                                   help="Ej: //aside[contains(@class,'related')] | //section[@id='relacionadas']")

    with colY:
        w_h2_list = st.checkbox("H2 (lista, SOLO dentro del artículo)", value=False, key="w_h2_list")
        w_h2_count = st.checkbox("H2 (cantidad, SOLO dentro del artículo)", value=False, key="w_h2_count")
        xp_h2 = st.text_input("XPath H2 (opcional)", value="", key="xp_h2",
                              help="Si empieza con .// se aplica respecto del contenedor; si no, se usa .//h2 por defecto.")
        w_h3_list = st.checkbox("H3 (lista, SOLO dentro del artículo)", value=False, key="w_h3_list")
        w_h3_count = st.checkbox("H3 (cantidad, SOLO dentro del artículo)", value=False, key="w_h3_count")
        xp_h3 = st.text_input("XPath H3 (opcional)", value="", key="xp_h3",
                              help="Si empieza con .// se aplica respecto del contenedor; si no, se usa .//h3 por defecto.")
        w_bold = st.checkbox("Cantidad de negritas (SOLO dentro del artículo)", value=False, key="w_bold")
        w_bold_list = st.checkbox("Lista de negritas (SOLO dentro del artículo)", value=False, key="w_bold_list")
        w_links = st.checkbox("Cantidad de links (SOLO dentro del artículo)", value=False, key="w_links")
        w_link_anchors = st.checkbox("Anchor text de links del artículo (lista)", value=False, key="w_link_anchors")
        w_tags = st.checkbox("Tags (lista)", value=False, key="w_tags")
        xp_tags = st.text_input("XPath Tags (opcional)", value="", key="xp_tags",
                                help="Ej: .//ul[@class='tags']//a | //meta[@property='article:tag']/@content")

    col8, col9, col10 = st.columns([1,1,2])
    with col8:
        concurrency = st.slider("Concurrencia", 2, 64, 24, step=2, key="fast_conc")
    with col9:
        timeout_s = st.slider("Timeout por página (s)", 5, 30, 12, step=1, key="fast_timeout")
    with col10:
        ua = st.text_input("User-Agent (opcional)", value="", key="fast_ua")
        if not ua.strip():
            st.caption("Sugerencia UA (si ves muchos 403):")
            st.code(_suggest_user_agent(""))

    joiner = st.text_input("Separador para listas (H2/H3/Tags/Anchors/Negritas)", value=" | ", key="joiner")

    # === Preflight GSC
    st.markdown("### 🔎 Semillas desde GSC")
    seeds = []
    seeds_search = []
    seeds_discover = []
    src_map = {"Search":"web","Discover":"discover","Ambos":"both"}
    src = src_map.get(tipo, "both")

    # Search
    if src in ("web","both"):
        seeds_search = _gsc_fetch_top_urls(
            sc_service, site_url, start_date, end_date, "web",
            country or None, device or None, order_by, int(row_limit)
        )
        st.write(f"**Search (web)**: {len(seeds_search):,} filas")
    # Discover
    if src in ("discover","both"):
        seeds_discover = _gsc_fetch_top_urls(
            sc_service, site_url, start_date, end_date, "discover",
            country or None, device or None, order_by, int(row_limit)
        )
        st.write(f"**Discover**: {len(seeds_discover):,} filas")

    if seeds_search:
        for r in seeds_search:
            r["source"] = "Search"
        seeds.extend(seeds_search)
    if seeds_discover:
        for r in seeds_discover:
            r["source"] = "Discover"
        seeds.extend(seeds_discover)

    if "_fast_error" in st.session_state:
        st.error(st.session_state["_fast_error"])

    df_seeds = pd.DataFrame(seeds)
    if not df_seeds.empty:
        # umbrales
        before = len(df_seeds)
        if min_clicks > 0:
            df_seeds = df_seeds[df_seeds["clicks"] >= int(min_clicks)]
        if min_impr > 0:
            df_seeds = df_seeds[df_seeds["impressions"] >= int(min_impr)]
        st.caption(f"Tras umbrales: {len(df_seeds):,} (antes {before:,})")

        # columnas útiles + CTR%
        df_seeds["ctr_pct"] = (df_seeds["ctr"].fillna(0) * 100).round(2)
        df_seeds = df_seeds.rename(columns={"page":"url"})
        df_seeds = df_seeds.sort_values(["url","clicks"], ascending=[True,False]).drop_duplicates(subset=["url"], keep="first")

        urls = df_seeds["url"].dropna().astype(str).tolist()
        if only_articles:
            urls = _filter_article_urls(urls)
        st.write(f"URLs candidatas a scraping: **{len(urls):,}**")
        st.code(urls[:20])

        # Botón de ejecutar
        can_run = len(urls) > 0
        if st.button("⚡ Ejecutar scraping + exportar a Sheets", type="primary", disabled=not can_run, key="fast_run"):
            ua_final = _suggest_user_agent(ua)

            # Armar wants/xpaths según checkboxes
            wants = {
                "title": w_title, "h1": w_h1, "meta_description": w_md,
                "og_title": w_ogt, "og_description": w_ogd, "canonical": w_canon,
                "published_time": w_pub, "lang": w_lang,
                "first_paragraph": w_firstp,
                "h2_list": w_h2_list, "h2_count": w_h2_count,
                "h3_list": w_h3_list, "h3_count": w_h3_count,
                "bold_count": w_bold, "bold_list": w_bold_list,
                "link_count": w_links, "link_anchor_texts": w_link_anchors,
                "related_links_count": w_rel_count, "related_link_anchors": w_rel_anchors,
                "tags_list": w_tags
            }
            xpaths = {
                "article": xp_article,
                "first_paragraph": xp_firstp,
                "h2": xp_h2,
                "h3": xp_h3,
                "tags": xp_tags,
                "related_box": xp_related
            }

            if not any(wants.values()):
                st.error("Seleccioná al menos un campo para extraer."); st.stop()

            try:
                # Scraping (async si hay aiohttp)
                try:
                    results = asyncio.run(_scrape_async(
                        urls, ua_final, wants=wants, xpaths=xpaths, joiner=joiner,
                        timeout_s=timeout_s, concurrency=int(concurrency)))
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    results = loop.run_until_complete(_scrape_async(
                        urls, ua_final, wants=wants, xpaths=xpaths, joiner=joiner,
                        timeout_s=timeout_s, concurrency=int(concurrency)))
                    loop.close()

                df_scr = pd.DataFrame(results)

                # Merge con métricas de GSC
                df_out = pd.merge(
                    df_seeds[["url","source","clicks","impressions","ctr_pct","position"]],
                    df_scr, on="url", how="left"
                )

                # Columnas dinámicas según wants
                cols = ["source","url"]
                # básicos
                if w_h1: cols.append("h1")
                if w_title: cols.append("title")
                if w_md: cols.append("meta_description")
                if w_ogt: cols.append("og_title")
                if w_ogd: cols.append("og_description")
                if w_canon: cols.append("canonical")
                if w_pub: cols.append("published_time")
                if w_lang: cols.append("lang")
                # avanzados
                if w_firstp: cols.append("first_paragraph")
                if w_h2_list: cols.append("h2_list")
                if w_h2_count: cols.append("h2_count")
                if w_h3_list: cols.append("h3_list")
                if w_h3_count: cols.append("h3_count")
                if w_bold: cols.append("bold_count")
                if w_bold_list: cols.append("bold_list")
                if w_links: cols.append("link_count")
                if w_link_anchors: cols.append("link_anchor_texts")
                if w_rel_count: cols.append("related_links_count")
                if w_rel_anchors: cols.append("related_link_anchors")
                if w_tags: cols.append("tags_list")
                # métricas
                cols += ["clicks","impressions","ctr_pct","position","status","error"]

                for c in cols:
                    if c not in df_out.columns:
                        df_out[c] = "" if c not in ("clicks","impressions","ctr_pct","position","status") else 0
                df_out = df_out[cols]

                # Crear Sheet en Drive
                name = f"H1/meta ({start_date} a {end_date}) - {site_url.replace('https://','').replace('http://','').strip('/')}"
                meta = {"name": name, "mimeType": "application/vnd.google-apps.spreadsheet"}
                parents = st.session_state.get("dest_folder_id")
                if parents:
                    meta["parents"] = [parents]
                newfile = drive_service.files().create(body=meta, fields="id,name,webViewLink").execute()
                sid = newfile["id"]

                # Escribir datos (gspread)
                sh = gs_client.open_by_key(sid)
                ws = sh.sheet1
                ws.resize(1)  # limpiar
                ws.update([df_out.columns.tolist()] + df_out.fillna("").astype(str).values.tolist())

                maybe_prefix_sheet_name_with_medio(drive_service, sid, site_url)

                st.success("¡Listo! Tu documento está creado.")
                st.markdown(f"➡️ **Abrir Google Sheets**: https://docs.google.com/spreadsheets/d/{sid}")
                with st.expander("Compartir acceso al documento (opcional)"):
                    share_controls(drive_service, sid, default_email=_me.get("emailAddress") if _me else None)

                activity_log_append(
                    drive_service, gs_client,
                    user_email=( _me or {}).get("emailAddress") or "",
                    event="analysis", site_url=site_url,
                    analysis_kind="Extractor rápido H1 (+metadatos)",
                    sheet_id=sid, sheet_name=name, sheet_url=f"https://docs.google.com/spreadsheets/d/{sid}",
                    gsc_account=st.session_state.get("src_account_label") or "",
                    notes=f"win={start_date}->{end_date}, src={tipo}, urls={len(urls)}, wants={ {k:v for k,v in wants.items() if v} }"
                )
                st.session_state["last_file_id"] = sid
                st.session_state["last_file_kind"] = "fast_h1"

                # Vista previa
                with st.expander("Vista previa (primeras 20 filas)"):
                    st.dataframe(df_out.head(20), use_container_width=True)

            except Exception as e:
                st.error(f"Falló el scraping o el volcado a Sheets: {e}")

    else:
        st.info("Ajustá la ventana o filtros para obtener semillas desde GSC.")

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