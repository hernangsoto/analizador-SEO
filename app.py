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
from modules.app_ext import (
    USING_EXT,
    run_core_update, run_evergreen, run_traffic_audit, run_names_analysis,
    run_discover_snoop, run_content_analysis, run_content_structure,   # <- NUEVO
)
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
    # EXTRACTOR / ESTRUCTURA
    opciones.append("10. Análisis de estructura de contenidos ✅")

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
# Utilidades simples (solo usadas en otros flujos)
# =========================

def _suggest_user_agent(ua: str | None) -> str:
    if ua and ua.strip():
        return ua
    return ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36")

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
        st.info("Este modo utiliza el runner externo de contenidos. Para estructura y metadatos, usá el análisis 10.")

elif analisis == "10":
    # ===== Análisis de estructura de contenidos (repo externo) =====
    st.subheader("Análisis de estructura de contenidos")
    st.caption("Trae URLs desde Search Console, aplica filtros, extrae campos del HTML y exporta a Sheets. El procesamiento vive en el paquete externo `seo_analisis_ext`.")

    # --- Config básica (fechas, origen) ---
    colA, colB, colC = st.columns([1,1,2])
    with colA:
        date_from = st.date_input("Desde (inclusive)", key="estr_date_from")
    with colB:
        date_to = st.date_input("Hasta (inclusive)", key="estr_date_to")
    with colC:
        source_label = st.radio("Origen", ["Search", "Discover", "Search + Discover"], horizontal=True, key="estr_source")

    # Máximo de URLs por origen (fuera del panel avanzado)
    row_limit = st.number_input("Máximo de URLs por origen", min_value=10, max_value=5000, value=500, step=10, key="estr_row_limit")

    # --- Opciones avanzadas (filtros + scraping) ---
    with st.expander("⚙️ Opciones avanzadas de configuración y filtrado", expanded=False):
        col1, col2, col3 = st.columns(3)
        with col1:
            country = st.text_input("País (ISO-3166-1 alpha-3, ej: ARG, USA, ESP)", value="", key="estr_country").strip().upper()
            min_clicks = st.number_input("Mínimo de clics", min_value=0, value=0, step=10, key="estr_min_clicks")
        with col2:
            device = st.selectbox("Dispositivo", ["", "DESKTOP", "MOBILE", "TABLET"], index=0, key="estr_device")
            min_impr = st.number_input("Mínimo de impresiones", min_value=0, value=0, step=100, key="estr_min_impr")
        with col3:
            order_by = st.selectbox("Ordenar por", ["clicks","impressions","ctr","position"], index=0, key="estr_order")
            only_articles = st.checkbox("Solo artículos (filtra tags/players/etc.)", value=True, key="estr_only_articles")

        st.markdown("---")
        colX, colY, colZ = st.columns([1,1,1])
        with colX:
            concurrency = st.slider("Concurrencia", 2, 64, 24, step=2, key="estr_conc")
            timeout_s = st.slider("Timeout por página (s)", 5, 30, 12, step=1, key="estr_timeout")
        with colY:
            ua = st.text_input("User-Agent (opcional)", value="", key="estr_ua")
            if not ua.strip():
                st.caption("Sugerencia UA (si ves 403):")
                st.code(_suggest_user_agent(""))
        with colZ:
            joiner = st.text_input("Separador para listas", value=" | ", key="estr_joiner")

    # --- Campos a extraer + XPaths ---
    st.markdown("### 🧲 Campos a extraer")

    # Lista de keys de widgets (para select/deselect all)
    FIELD_KEYS = [
        "w_title","w_h1","w_md","w_ogt","w_ogd","w_canon","w_pub","w_upd","w_author","w_lang",
        "w_firstp","w_h2_list","w_h2_count","w_h3_list","w_h3_count","w_bold","w_bold_list",
        "w_links","w_link_anchors","w_rel_count","w_rel_anchors","w_tags","w_entities"
    ]
    # Defaults iniciales una sola vez
    DEFAULTS = {
        "w_title": True, "w_h1": True, "w_md": True, "w_ogt": False, "w_ogd": False, "w_canon": True,
        "w_pub": False, "w_upd": False, "w_author": False, "w_lang": False,
        "w_firstp": True,
        "w_h2_list": False, "w_h2_count": False, "w_h3_list": False, "w_h3_count": False,
        "w_bold": False, "w_bold_list": False,
        "w_links": False, "w_link_anchors": False,
        "w_rel_count": False, "w_rel_anchors": False,
        "w_tags": False,
        "w_entities": False,
    }
    for k,v in DEFAULTS.items():
        if k not in st.session_state:
            st.session_state[k] = v

    colSelA, colSelB = st.columns([1,1])
    with colSelA:
        if st.button("Seleccionar todo", key="estr_sel_all"):
            for k in FIELD_KEYS:
                st.session_state[k] = True
            st.rerun()
    with colSelB:
        if st.button("Deseleccionar todo", key="estr_desel_all"):
            for k in FIELD_KEYS:
                st.session_state[k] = False
            st.rerun()

    colL, colR = st.columns(2)
    with colL:
        w_title = st.checkbox("Title", key="w_title", value=st.session_state["w_title"])
        w_h1 = st.checkbox("H1", key="w_h1", value=st.session_state["w_h1"])
        w_md = st.checkbox("Meta Description", key="w_md", value=st.session_state["w_md"])
        w_ogt = st.checkbox("OG:title", key="w_ogt", value=st.session_state["w_ogt"])
        w_ogd = st.checkbox("OG:description", key="w_ogd", value=st.session_state["w_ogd"])
        w_canon = st.checkbox("Canonical", key="w_canon", value=st.session_state["w_canon"])
        w_pub = st.checkbox("Fecha publicación", key="w_pub", value=st.session_state["w_pub"])
        w_upd = st.checkbox("Fecha actualización", key="w_upd", value=st.session_state["w_upd"])
        w_author = st.checkbox("Autor", key="w_author", value=st.session_state["w_author"])
        w_lang = st.checkbox("Lang", key="w_lang", value=st.session_state["w_lang"])
        w_firstp = st.checkbox("Primer párrafo", key="w_firstp", value=st.session_state["w_firstp"])

        # XPaths principales
        xp_article = st.text_input("XPath contenedor del artículo (recomendado)", value=st.session_state.get("xp_article",""), key="xp_article",
            help="Define el scope de h2/h3/negritas/links. Ej: //article | //main[@id='content']")
        xp_firstp = st.text_input("XPath Primer párrafo (opcional)", value=st.session_state.get("xp_firstp",""), key="xp_firstp")

        st.markdown("**Caja de noticias relacionadas**")
        w_rel_count = st.checkbox("Cantidad de links en relacionadas", key="w_rel_count", value=st.session_state["w_rel_count"])
        w_rel_anchors = st.checkbox("Anchors de relacionadas (lista)", key="w_rel_anchors", value=st.session_state["w_rel_anchors"])
        xp_related = st.text_input("XPath de la caja de relacionadas", value=st.session_state.get("xp_related",""), key="xp_related")

    with colR:
        w_h2_list = st.checkbox("H2 (lista, SOLO dentro del artículo)", key="w_h2_list", value=st.session_state["w_h2_list"])
        w_h2_count = st.checkbox("H2 (cantidad, SOLO dentro del artículo)", key="w_h2_count", value=st.session_state["w_h2_count"])
        xp_h2 = st.text_input("XPath H2 (opcional)", value=st.session_state.get("xp_h2",""), key="xp_h2")

        w_h3_list = st.checkbox("H3 (lista, SOLO dentro del artículo)", key="w_h3_list", value=st.session_state["w_h3_list"])
        w_h3_count = st.checkbox("H3 (cantidad, SOLO dentro del artículo)", key="w_h3_count", value=st.session_state["w_h3_count"])
        xp_h3 = st.text_input("XPath H3 (opcional)", value=st.session_state.get("xp_h3",""), key="xp_h3")

        w_bold = st.checkbox("Negritas (cantidad, SOLO dentro del artículo)", key="w_bold", value=st.session_state["w_bold"])
        w_bold_list = st.checkbox("Negritas (lista, SOLO dentro del artículo)", key="w_bold_list", value=st.session_state["w_bold_list"])

        w_links = st.checkbox("Links (cantidad, SOLO dentro del artículo)", key="w_links", value=st.session_state["w_links"])
        w_link_anchors = st.checkbox("Links (anchors del artículo)", key="w_link_anchors", value=st.session_state["w_link_anchors"])

        w_tags = st.checkbox("Tags (lista)", key="w_tags", value=st.session_state["w_tags"])
        xp_tags = st.text_input("XPath Tags (opcional)", value=st.session_state.get("xp_tags",""), key="xp_tags")

        w_entities = st.checkbox("Detectar entidades con spaCy (opcional)", key="w_entities", value=st.session_state["w_entities"])

    # --- Construir params para el runner externo ---
    source_map = {"Search":"search","Discover":"discover","Search + Discover":"both"}
    params = {
        "date_from": date_from,
        "date_to": date_to,
        "source": source_map.get(source_label, "both"),
        "row_limit": int(row_limit),
        "country": (st.session_state.get("estr_country") or "").strip().upper() or None,
        "device": (st.session_state.get("estr_device") or "").strip().upper() or None,
        "order_by": st.session_state.get("estr_order","clicks"),
        "only_articles": bool(st.session_state.get("estr_only_articles", True)),
        "min_clicks": int(st.session_state.get("estr_min_clicks", 0)),
        "min_impressions": int(st.session_state.get("estr_min_impr", 0)),
        "concurrency": int(st.session_state.get("estr_conc", 24)),
        "timeout_s": int(st.session_state.get("estr_timeout", 12)),
        "ua": st.session_state.get("estr_ua",""),
        "joiner": st.session_state.get("estr_joiner"," | "),
        "entities": bool(st.session_state.get("w_entities", False)),
        "sheet_title_prefix": "Estructura contenidos",
        "wants": {
            "title": bool(st.session_state.get("w_title")),
            "h1": bool(st.session_state.get("w_h1")),
            "meta_description": bool(st.session_state.get("w_md")),
            "og_title": bool(st.session_state.get("w_ogt")),
            "og_description": bool(st.session_state.get("w_ogd")),
            "canonical": bool(st.session_state.get("w_canon")),
            "published_time": bool(st.session_state.get("w_pub")),
            "updated_time": bool(st.session_state.get("w_upd")),
            "author": bool(st.session_state.get("w_author")),
            "lang": bool(st.session_state.get("w_lang")),
            "first_paragraph": bool(st.session_state.get("w_firstp")),
            "h2_list": bool(st.session_state.get("w_h2_list")),
            "h2_count": bool(st.session_state.get("w_h2_count")),
            "h3_list": bool(st.session_state.get("w_h3_list")),
            "h3_count": bool(st.session_state.get("w_h3_count")),
            "bold_count": bool(st.session_state.get("w_bold")),
            "bold_list": bool(st.session_state.get("w_bold_list")),
            "link_count": bool(st.session_state.get("w_links")),
            "link_anchor_texts": bool(st.session_state.get("w_link_anchors")),
            "related_links_count": bool(st.session_state.get("w_rel_count")),
            "related_link_anchors": bool(st.session_state.get("w_rel_anchors")),
            "tags_list": bool(st.session_state.get("w_tags")),
        },
        "xpaths": {
            "article": st.session_state.get("xp_article",""),
            "first_paragraph": st.session_state.get("xp_firstp",""),
            "h2": st.session_state.get("xp_h2",""),
            "h3": st.session_state.get("xp_h3",""),
            "tags": st.session_state.get("xp_tags",""),
            "related_box": st.session_state.get("xp_related",""),
            "author": "",          # opcional: podés completarlo si tenés un XPath específico
            "updated_time": "",    # opcional
        }
    }

    # Semillas desde GSC — SOLO en modo debug (se muestra el payload; el runner hace la query real)
    if st.session_state.get("DEBUG"):
        with st.expander("🔎 Semillas desde GSC (debug)"):
            st.write("Las semillas se obtienen en el runner externo con estos parámetros:")
            debug_view = {
                "date_from": str(params["date_from"]),
                "date_to": str(params["date_to"]),
                "source": params["source"],
                "row_limit": params["row_limit"],
                "country": params["country"],
                "device": params["device"],
                "order_by": params["order_by"],
                "only_articles": params["only_articles"],
                "min_clicks": params["min_clicks"],
                "min_impressions": params["min_impressions"]
            }
            st.code(debug_view, language="json")

    # --- Ejecutar ---
    if run_content_structure is None:
        st.error("Este despliegue no incluye `run_content_structure` del repo externo `seo_analisis_ext`.")
    else:
        can_run = (date_from is not None) and (date_to is not None)
        if st.button("⚡ Ejecutar y exportar a Sheets", type="primary", disabled=not can_run, key="estr_run"):
            def _log(msg: str):
                st.caption(msg)

            try:
                sid = run_with_indicator(
                    "Procesando Análisis de estructura de contenidos",
                    run_content_structure,
                    sc_service, drive_service, gs_client, site_url,
                    params,
                    st.session_state.get("dest_folder_id"),
                    _log
                )
                maybe_prefix_sheet_name_with_medio(drive_service, sid, site_url)
                st.success("¡Listo! Tu documento está creado.")
                st.markdown(f"➡️ **Abrir Google Sheets**: https://docs.google.com/spreadsheets/d/{sid}")
                with st.expander("Compartir acceso al documento (opcional)"):
                    share_controls(drive_service, sid, default_email=_me.get("emailAddress") if _me else None)

                # Log de actividad
                activity_log_append(
                    drive_service, gs_client,
                    user_email=( _me or {}).get("emailAddress") or "",
                    event="analysis", site_url=site_url,
                    analysis_kind="Estructura contenidos",
                    sheet_id=sid, sheet_name="", sheet_url=f"https://docs.google.com/spreadsheets/d/{sid}",
                    gsc_account=st.session_state.get("src_account_label") or "",
                    notes=f"win={params['date_from']}->{params['date_to']}, src={params['source']}"
                )
                st.session_state["last_file_id"] = sid
                st.session_state["last_file_kind"] = "content_structure"

            except Exception as e:
                st.error(f"Fallo en el runner externo: {e}")

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