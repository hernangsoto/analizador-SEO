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
    # NUEVO: extractor rápido H1 + metadatos
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
def _parse_html_for_meta(html: str) -> dict:
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except Exception:
        return {
            "h1": "", "title": "", "meta_description": "", "og_title": "", "og_description": "",
            "canonical": "", "published_time": "", "lang": ""
        }
    parser = "lxml"
    try:
        soup = BeautifulSoup(html, parser)
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    def _meta_content(**kw):
        tag = soup.find("meta", attrs=kw)
        return (tag.get("content") or "").strip() if tag else ""

    h1 = ""
    h1_el = soup.find("h1")
    if h1_el:
        h1 = h1_el.get_text(strip=True)

    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()

    meta_description = _meta_content(name="description") or _meta_content(property="description")
    og_title = _meta_content(property="og:title")
    og_description = _meta_content(property="og:description")
    canonical = ""
    link_canon = soup.find("link", rel=lambda v: v and "canonical" in [x.lower() for x in (v if isinstance(v, list) else [v])])
    if link_canon:
        canonical = (link_canon.get("href") or "").strip()

    # published_time → varias heurísticas
    published_time = _meta_content(property="article:published_time") or \
                     _meta_content(name="pubdate") or \
                     _meta_content(name="date") or ""
    if not published_time:
        time_tag = soup.find("time")
        if time_tag:
            published_time = (time_tag.get("datetime") or "").strip() or time_tag.get_text(strip=True)

    lang = ""
    html_tag = soup.find("html")
    if html_tag:
        lang = (html_tag.get("lang") or "").strip()

    return {
        "h1": h1, "title": title, "meta_description": meta_description,
        "og_title": og_title, "og_description": og_description,
        "canonical": canonical, "published_time": published_time, "lang": lang
    }

async def _fetch_one(session, url: str, ua: str, timeout_s: int) -> dict:
    out = {"url": url, "ok": False, "status": 0, "error": "", **{k:"" for k in [
        "h1","title","meta_description","og_title","og_description","canonical","published_time","lang"
    ]}}
    try:
        async with session.get(url, headers={"User-Agent": ua}, timeout=timeout_s, allow_redirects=True) as resp:
            out["status"] = resp.status
            if resp.status >= 400:
                out["error"] = f"http {resp.status}"
                return out
            html = await resp.text(errors="ignore")
            meta = _parse_html_for_meta(html)
            out.update(meta)
            out["ok"] = True
            return out
    except Exception as e:
        out["error"] = str(e)
        return out

async def _scrape_async(urls: list[str], ua: str, timeout_s: int = 12, concurrency: int = 20) -> list[dict]:
    # aiohttp es opcional
    try:
        import aiohttp  # type: ignore
    except Exception:
        return _scrape_sync(urls, ua, timeout_s, concurrency)
    connector = aiohttp.TCPConnector(limit=concurrency, ssl=False)
    timeout = aiohttp.ClientTimeout(total=max(timeout_s+2, timeout_s))
    results: list[dict] = []
    sem = asyncio.Semaphore(concurrency)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout, trust_env=True) as session:
        async def _bound(u):
            async with sem:
                return await _fetch_one(session, u, ua, timeout_s)
        tasks = [_bound(u) for u in urls]
        # barra de progreso
        done = 0
        progress = st.progress(0.0, text="Scrapeando páginas…")
        for coro in asyncio.as_completed(tasks):
            res = await coro
            results.append(res)
            done += 1
            progress.progress(done/len(tasks), text=f"Scrapeando páginas… {done}/{len(tasks)}")
        progress.empty()
    # mantener orden según urls
    order = {u:i for i,u in enumerate(urls)}
    results.sort(key=lambda r: order.get(r.get("url",""), 1e9))
    return results

def _scrape_sync(urls: list[str], ua: str, timeout_s: int = 12, concurrency: int = 12) -> list[dict]:
    try:
        import requests
    except Exception as e:
        return [{"url": u, "ok": False, "status": 0, "error": f"requests no disponible: {e}"} for u in urls]
    from concurrent.futures import ThreadPoolExecutor, as_completed
    results: list[dict] = []
    headers = {"User-Agent": ua}

    def _one(u: str) -> dict:
        out = {"url": u, "ok": False, "status": 0, "error": "", **{k:"" for k in [
            "h1","title","meta_description","og_title","og_description","canonical","published_time","lang"
        ]}}
        try:
            rs = requests.get(u, headers=headers, timeout=timeout_s, allow_redirects=True)
            out["status"] = rs.status_code
            if rs.status_code >= 400:
                out["error"] = f"http {rs.status_code}"
                return out
            meta = _parse_html_for_meta(rs.text)
            out.update(meta)
            out["ok"] = True
        except Exception as e:
            out["error"] = str(e)
        return out

    progress = st.progress(0.0, text="Scrapeando páginas…")
    done = 0
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futs = [ex.submit(_one, u) for u in urls]
        for f in as_completed(futs):
            results.append(f.result())
            done += 1
            progress.progress(done/len(futs), text=f"Scrapeando páginas… {done}/{len(futs)}")
    progress.empty()
    # mantener orden
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
        # (Contenido original del modo 9 omitido por brevedad en esta versión de trabajo)
        st.warning("El modo 9 está disponible, pero se recomienda usar el 10 para H1+metadatos directos desde GSC.")

elif analisis == "10":
    # ===== EXTRACTOR RÁPIDO: GSC → URLs → H1 (+ metadatos) → Sheets =====
    st.subheader("Extractor rápido desde Search Console")
    st.caption("Trae URLs por Search / Discover (o ambos), filtra por país / dispositivo, scrapea rápido H1 y metadatos, y los publica en Sheets.")

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
        # quitar duplicados priorizando el mayor clicks
        df_seeds = df_seeds.sort_values(["url","clicks"], ascending=[True,False])
        df_seeds = df_seeds.drop_duplicates(subset=["url"], keep="first")

        urls = df_seeds["url"].dropna().astype(str).tolist()
        if only_articles:
            urls = _filter_article_urls(urls)
        st.write(f"URLs candidatas a scraping: **{len(urls):,}**")
        st.code(urls[:20])

        # Botón de ejecutar
        can_run = len(urls) > 0
        if st.button("⚡ Ejecutar scraping + exportar a Sheets", type="primary", disabled=not can_run, key="fast_run"):
            ua_final = _suggest_user_agent(ua)
            try:
                # Scraping (async si hay aiohttp)
                try:
                    results = asyncio.run(_scrape_async(urls, ua_final, timeout_s=timeout_s, concurrency=int(concurrency)))
                except RuntimeError:
                    # si ya hay loop (p.ej. en ciertos deploys), usar crear/usar nuevo loop
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    results = loop.run_until_complete(_scrape_async(urls, ua_final, timeout_s=timeout_s, concurrency=int(concurrency)))
                    loop.close()

                df_scr = pd.DataFrame(results)
                # Merge con métricas de GSC
                df_out = pd.merge(
                    df_seeds[["url","source","clicks","impressions","ctr_pct","position"]],
                    df_scr, on="url", how="left"
                )

                # Orden y columnas finales
                cols = [
                    "source","url","h1","title","meta_description","og_title","og_description",
                    "canonical","published_time","lang",
                    "clicks","impressions","ctr_pct","position","status","error"
                ]
                for c in cols:
                    if c not in df_out.columns:
                        df_out[c] = "" if c not in ("clicks","impressions","ctr_pct","position","status") else 0
                df_out = df_out[cols]

                # Crear Sheet en Drive
                name = f"H1 - {site_url.replace('https://','').replace('http://','').strip('/')} - {start_date} a {end_date}"
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
                    notes=f"win={start_date}->{end_date}, src={tipo}, urls={len(urls)}"
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