from __future__ import annotations

# --- Permisos OAuth en localhost + tolerancia de scope
import os
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

import sys
import json
from types import SimpleNamespace
from datetime import date, timedelta, datetime
from urllib.parse import urlsplit

import pandas as pd
import streamlit as st
from google.oauth2.credentials import Credentials

# Concurrencia / red
import concurrent.futures

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

# ====== UI / Branding (módulos locales) ======
from modules.ui import apply_page_style, get_user, sidebar_user_info, login_screen
from modules.app_config import apply_base_style_and_logo, get_app_home
from modules.app_ext import USING_EXT, run_core_update, run_evergreen, run_traffic_audit, run_names_analysis

# Descubrir si hay funciones externas opcionales
try:
    from modules.app_ext import run_discover_snoop
except Exception:
    run_discover_snoop = None

try:
    from modules.app_ext import run_content_analysis
except Exception:
    run_content_analysis = None

from modules.app_utils import get_qp, clear_qp, has_gsc_scope, norm
from modules.app_ai import load_prompts, gemini_healthcheck, gemini_summary
from modules.app_params import (
    params_for_core_update, params_for_evergreen, params_for_auditoria, params_for_names,
)

try:
    from modules.app_params import params_for_discover_snoop
except Exception:
    params_for_discover_snoop = lambda: {}

try:
    from modules.app_params import params_for_content
except Exception:
    params_for_content = None

from modules.app_activity import maybe_prefix_sheet_name_with_medio, activity_log_append
from modules.app_errors import run_with_indicator
from modules.app_auth_flow import step0_google_identity, logout_screen

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
#nmd-band, .nmd-band, [data-nmd="band"], [id*="band"], [class*="band"] {
  display: none !important; height:0 !important; margin:0 !important; padding:0 !important;
}
div[data-testid="stAppViewContainer"] { padding-top: 0 !important; }
main .block-container { margin-top: 0 !important; padding-top: .75rem !important; }
header[data-testid="stHeader"] { z-index: 1500 !important; }
:root:has([data-testid="stSidebar"][aria-expanded="true"])
  header[data-testid="stHeader"]::before { left: 350px !important; }
:root:has([data-testid="stSidebar"][aria-expanded="false"])
  header[data-testid="stHeader"]::before { left: 100px !important; }
:root:not(:has([data-testid="stSidebar"])) header[data-testid="stHeader"]::before { left: 16px !important; }
.small-note { font-size: 12px; color: #666; }
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
    opciones.append("9. Extractor rápido (GSC + Scrape) ✅")
    key = st.radio("Tipos disponibles:", opciones, index=len(opciones)-1, key="analysis_choice")
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
                    run_discover_snoop,
                    drive_service, gs_client,
                    df, params_ds,
                    st.session_state.get("dest_folder_id")
                )
                st.success("¡Listo! Tu documento está creado.")
                st.markdown(f"➡️ **Abrir Google Sheets**: https://docs.google.com/spreadsheets/d/{sid}")
                with st.expander("Compartir acceso al documento (opcional)"):
                    share_controls(drive_service, sid, default_email=_me.get("emailAddress") if _me else None)
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
    from google_auth_oauthlib.flow import Flow
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
                        order_by: str, row_limit: int, descending: bool = True) -> list[dict]:
    try:
        body = {
            "startDate": str(start),
            "endDate": str(end),
            "dimensions": ["page"],
            "rowLimit": int(row_limit),
            "startRow": 0,
            "type": search_type,  # "web" | "discover"
            "orderBy": [{"field": order_by, "descending": bool(descending)}],
        }
        filters = []
        if country:
            filters.append({"dimension": "country","operator": "equals","expression": _iso3_lower(country)})
        if device:
            filters.append({"dimension": "device","operator": "equals","expression": _device_upper(device)})
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
        st.session_state["_simple_error"] = f"GSC query error ({search_type}): {e}"
        return []

_DROP_PATTERNS = (
    "/player/", "/tag/", "/tags/", "/etiqueta/", "/categoria/", "/category/",
    "/author/", "/autores/", "/programas/", "/hd/", "/podcast", "/videos/",
    "/video/", "/envivo", "/en-vivo", "/en_vivo", "/live", "/player-", "?"
)
def _looks_like_article(u: str) -> bool:
    if not u: return False
    u = u.strip().lower()
    if u in ("https://", "http://"): return False
    if u.endswith((".jpg",".jpeg",".png",".gif",".svg",".webp",".mp4",".mp3",".m3u8",".pdf")):
        return False
    if u.count("/") <= 3:
        return False
    for p in _DROP_PATTERNS:
        if p in u:
            return False
    return True

def _suggest_user_agent(ua: str | None) -> str:
    if ua and ua.strip():
        return ua.strip()
    return ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36")

def _host_from_site(site: str) -> str:
    try:
        netloc = urlsplit(site).netloc or site.replace("sc-domain:", "")
        return netloc.replace("www.", "")
    except Exception:
        return site.replace("https://","").replace("http://","").strip("/")

# =========================
# 9) Extractor rápido (GSC + Scrape)
# =========================
if analisis == "9":
    st.subheader("Extractor rápido (GSC + Scrape)")

    # ---- Bloque superior: fechas, origen y límite por origen
    c1, c2, c3 = st.columns([1,1,1.6])
    with c1:
        start_date = st.date_input("📅 Desde", value=(date.today() - timedelta(days=28)))
    with c2:
        end_date = st.date_input("📅 Hasta", value=(date.today() - timedelta(days=1)))
    with c3:
        origen = st.radio(
            "Origen",
            options=["Search", "Discover", "Search + Discover"],
            horizontal=True,
            index=2
        )

    max_urls = st.number_input("Máximo de URLs por origen", min_value=1, max_value=5000, value=300, step=50)

    # ---- Opciones avanzadas (filtros + scraping técnico)
    with st.expander("⚙️ Opciones avanzadas de configuración / filtrado", expanded=False):
        cA1, cA2, cA3 = st.columns([1,1,1])
        with cA1:
            country = st.text_input("País (ISO-3, ej. ARG, MEX, USA)", value="").strip()
        with cA2:
            device = st.selectbox("Dispositivo", options=["(todos)","Desktop","Mobile","Tablet"], index=0)
            device_val = None if device.startswith("(") else device
        with cA3:
            order_by = st.selectbox("Ordenar por", options=["clicks","impressions","ctr","position"], index=0)

        cB1, cB2, cB3 = st.columns([1,1,1])
        with cB1:
            min_clicks = st.number_input("Mínimo de clics", min_value=0, value=0, step=10)
        with cB2:
            min_impr = st.number_input("Mínimo de impresiones", min_value=0, value=0, step=100)
        with cB3:
            only_articles = st.checkbox("Solo artículos (descartar players, tags, etc.)", value=True)

        st.markdown("---")
        cC1, cC2, cC3, cC4 = st.columns([1,1,1,1])
        with cC1:
            concurrency = st.number_input("Concurrencia (hilos)", min_value=1, max_value=64, value=8, step=1)
        with cC2:
            timeout = st.number_input("Timeout por página (segundos)", min_value=3, max_value=60, value=12, step=1)
        with cC3:
            list_sep = st.text_input("Separador para listas", value=" | ")
        with cC4:
            user_agent_in = st.text_input("User-Agent (opcional, dejamos uno sugerido si está vacío)", value="")
        if not user_agent_in.strip():
            st.caption("Sugerencia de User-Agent:")
            st.code(_suggest_user_agent(None))

    # =========================
    # Campos a extraer (agrupados por subtítulos)
    # =========================
    st.subheader("🧲 Campos a extraer")

    # Titulación
    st.markdown("#### 📑 Titulación")
    colT1, colT2, colT3 = st.columns([1,1,1.5])
    with colT1:
        want_title = st.checkbox("TITLE", value=True)
    with colT2:
        want_h1 = st.checkbox("H1", value=True)
    with colT3:
        want_og = st.checkbox("OG Title (og:title)", value=True)
    h1_selector = st.text_input("Selector para H1 (CSS o XPath, opcional)", value="").strip()

    # Artículo, H2 y H3
    st.markdown("#### 📰 Artículo, H2 y H3")
    article_xpath = st.text_input("XPath del contenedor del artículo (recomendado)", value="//article").strip()
    colA1, colA2 = st.columns([1,1])
    with colA1:
        want_first_p = st.checkbox("Primer párrafo (dentro del artículo)", value=True)
    with colA2:
        first_p_xpath = st.text_input("XPath para primer párrafo (opcional)", value=".//p[1]").strip()
    colH = st.columns([1,1])
    with colH[0]:
        want_h2 = st.checkbox("Extraer lista de H2 (dentro del artículo)", value=True)
    with colH[1]:
        want_h3 = st.checkbox("Extraer lista de H3 (dentro del artículo)", value=False)

    # Negritas y links
    st.markdown("#### 🅱️ Negritas y links")
    colB1, colB2, colB3, colB4 = st.columns([1,1,1,1])
    with colB1:
        want_bold_count = st.checkbox("Cantidad de negritas (b/strong) en el artículo", value=True)
    with colB2:
        want_bold_list  = st.checkbox("Lista de negritas del artículo", value=False)
    with colB3:
        want_links_count = st.checkbox("Cantidad de links en el artículo", value=True)
    with colB4:
        want_links_anchors = st.checkbox("Anchor text de links en el artículo", value=False)

    # Tags
    st.markdown("#### 🏷️ Tags")
    tags_xpath = st.text_input("XPath de ítems de tag (ej: //a[contains(@class,'tag')])", value="").strip()
    colTG1, colTG2 = st.columns([1,1])
    with colTG1:
        want_tags_list = st.checkbox("Lista de tags", value=False)
    with colTG2:
        want_tags_count = st.checkbox("Cantidad de tags", value=False)

    # Caja de noticias relacionadas
    st.markdown("#### 🗂️ Caja de noticias relacionadas")
    colR0, colR1 = st.columns([1,2])
    with colR0:
        want_related = st.checkbox("Extraer caja de relacionadas (por XPath)", value=False)
    with colR1:
        related_xpath = st.text_input("XPath del contenedor de relacionadas", value="").strip()
    colR2, colR3 = st.columns([1,1])
    with colR2:
        want_related_count = st.checkbox("Cantidad de links en relacionadas", value=True, disabled=not want_related)
    with colR3:
        want_related_anchors = st.checkbox("Anchors de relacionadas", value=True, disabled=not want_related)

    # =========================
    # Implementación: Scraper
    # =========================
    try:
        import requests
        from bs4 import BeautifulSoup  # type: ignore
    except Exception as e:
        st.error(f"No puedo importar requests/bs4: {e}")
        st.stop()

    try:
        from lxml import html as lxml_html  # type: ignore
        _HAS_LXML = True
    except Exception:
        _HAS_LXML = False

    def _parse_all(text: str):
        """Devuelve (soup, tree) donde tree puede ser None si no hay lxml."""
        soup = BeautifulSoup(text, "lxml") if "lxml" in sys.modules else BeautifulSoup(text, "html.parser")
        tree = None
        if _HAS_LXML:
            try:
                tree = lxml_html.fromstring(text)
            except Exception:
                tree = None
        return soup, tree

    def _select_texts_css(soup, css: str) -> list[str]:
        try:
            els = soup.select(css)
            return [e.get_text(" ", strip=True) for e in els if e]
        except Exception:
            return []

    def _select_texts_xpath(tree, xpath: str) -> list[str]:
        if not (tree and _HAS_LXML):
            return []
        try:
            els = tree.xpath(xpath)
            out = []
            for el in els:
                try:
                    if isinstance(el, str):
                        out.append(el.strip())
                    else:
                        out.append(el.text_content().strip())
                except Exception:
                    pass
            return [t for t in out if t]
        except Exception:
            return []

    def _one_text_css(soup, css: str) -> str:
        try:
            el = soup.select_one(css)
            return el.get_text(" ", strip=True) if el else ""
        except Exception:
            return ""

    def _one_text_xpath(tree, xpath: str) -> str:
        vals = _select_texts_xpath(tree, xpath)
        return vals[0] if vals else ""

    def _extract_title(soup) -> str:
        try:
            t = soup.title.get_text(" ", strip=True) if soup.title else ""
            return t or ""
        except Exception:
            return ""

    def _extract_og_title(soup) -> str:
        try:
            el = soup.find("meta", attrs={"property":"og:title"}) or soup.find("meta", attrs={"name":"og:title"})
            return (el.get("content") or "").strip() if el else ""
        except Exception:
            return ""

    def _detect_selector_kind(s: str) -> str:
        s = (s or "").strip()
        if not s: return "auto"
        # Heurística simple
        if s.startswith("/") or s.startswith(".//") or s.startswith("//"):
            return "xpath"
        if "[" in s and "]" in s and "@" in s:
            return "xpath"
        return "css"

    def _get_h1(soup, tree, selector: str) -> str:
        if not selector:
            # auto
            txt = _one_text_css(soup, "h1")
            if not txt and tree:
                txt = _one_text_xpath(tree, "//h1[1]")
            return txt
        kind = _detect_selector_kind(selector)
        if kind == "xpath":
            return _one_text_xpath(tree, selector) if tree else ""
        return _one_text_css(soup, selector)

    def _scoped_tree(tree, article_xpath: str):
        if not (_HAS_LXML and tree and article_xpath.strip()):
            return None
        try:
            nodes = tree.xpath(article_xpath)
            if not nodes:
                return None
            return nodes[0]
        except Exception:
            return None

    def _collect_inside(node, xpath_rel: str) -> list[str]:
        """node es un elemento lxml; xpath_rel debe empezar con .// para ser relativo."""
        if not node or not _HAS_LXML:
            return []
        try:
            els = node.xpath(xpath_rel)
            out = []
            for el in els:
                if isinstance(el, str):
                    el = el.strip()
                    if el: out.append(el)
                else:
                    txt = el.text_content().strip()
                    if txt: out.append(txt)
            return out
        except Exception:
            return []

    def _anchors_inside(node, rel_xpath: str) -> list[str]:
        if not node or not _HAS_LXML:
            return []
        try:
            anchors = []
            els = node.xpath(rel_xpath)
            for el in els:
                if hasattr(el, "text_content"):
                    txt = el.text_content().strip()
                    if txt:
                        anchors.append(txt)
            return anchors
        except Exception:
            return []

    def _bolds_inside(node) -> list[str]:
        if not node or not _HAS_LXML:
            return []
        try:
            els = node.xpath(".//b | .//strong")
            out = []
            for el in els:
                txt = el.text_content().strip()
                if txt:
                    out.append(txt)
            return out
        except Exception:
            return []

    def _links_inside(node) -> list[str]:
        if not node or not _HAS_LXML:
            return []
        try:
            els = node.xpath(".//a")
            out = []
            for el in els:
                txt = el.text_content().strip()
                if txt:
                    out.append(txt)
            return out
        except Exception:
            return []

    def _related_from_xpath(tree, related_xpath: str) -> list[str]:
        if not (_HAS_LXML and tree and related_xpath.strip()):
            return []
        try:
            nodes = tree.xpath(related_xpath)
            if not nodes:
                return []
            node = nodes[0]
            return _anchors_inside(node, ".//a")
        except Exception:
            return []

    def _tags_from_xpath(tree, tags_xpath: str) -> list[str]:
        if not (_HAS_LXML and tree and tags_xpath.strip()):
            return []
        try:
            els = tree.xpath(tags_xpath)
            out = []
            for el in els:
                if hasattr(el, "text_content"):
                    txt = el.text_content().strip()
                    if txt:
                        out.append(txt)
                elif isinstance(el, str):
                    if el.strip():
                        out.append(el.strip())
            return out
        except Exception:
            return []

    def _scrape_one(url: str, timeout: int, ua: str, opts: dict) -> dict:
        data = {
            "url": url,
            "title": "", "h1": "", "og_title": "",
            "first_paragraph": "",
            "h2_list": [], "h3_list": [],
            "bold_list": [], "link_anchors": [],
            "tags_list": [], "related_anchors": [],
            "_errors": ""
        }
        try:
            headers = {"User-Agent": ua}
            rs = requests.get(url, headers=headers, timeout=timeout)
            rs.raise_for_status()
            soup, tree = _parse_all(rs.text)

            # Titulación
            if opts["want_title"]:
                data["title"] = _extract_title(soup)
            if opts["want_og"]:
                data["og_title"] = _extract_og_title(soup)
            if opts["want_h1"]:
                data["h1"] = _get_h1(soup, tree, opts["h1_selector"])

            # Nodo artículo (scoped)
            article_node = _scoped_tree(tree, opts["article_xpath"]) if _HAS_LXML else None

            # Primer párrafo
            if opts["want_first_p"]:
                if article_node and opts["first_p_xpath"]:
                    fp = _collect_inside(article_node, opts["first_p_xpath"])
                    data["first_paragraph"] = fp[0] if fp else ""
                elif article_node:
                    fp = _collect_inside(article_node, ".//p[1]")
                    data["first_paragraph"] = fp[0] if fp else ""
                else:
                    data["first_paragraph"] = _one_text_css(soup, "p")

            # H2 / H3 (solo dentro de artículo)
            if opts["want_h2"]:
                data["h2_list"] = _collect_inside(article_node, ".//h2") if article_node else []
            if opts["want_h3"]:
                data["h3_list"] = _collect_inside(article_node, ".//h3") if article_node else []

            # Negritas (solo dentro de artículo)
            if opts["want_bold_list"] or opts["want_bold_count"]:
                data["bold_list"] = _bolds_inside(article_node) if article_node else []

            # Links (solo dentro de artículo)
            if opts["want_links_anchors"] or opts["want_links_count"]:
                data["link_anchors"] = _links_inside(article_node) if article_node else []

            # Tags (por XPath)
            if opts["tags_xpath"] and (opts["want_tags_list"] or opts["want_tags_count"]):
                data["tags_list"] = _tags_from_xpath(tree, opts["tags_xpath"]) if tree else []

            # Relacionadas (por XPath contenedor)
            if opts["want_related"] and opts["related_xpath"]:
                data["related_anchors"] = _related_from_xpath(tree, opts["related_xpath"]) if tree else []

        except Exception as e:
            data["_errors"] = f"{e.__class__.__name__}: {e}"
        return data

    # =========================
    # 1) Traer URLs desde GSC
    # =========================
    if start_date > end_date:
        st.error("La fecha **Desde** no puede ser posterior a **Hasta**.")
        st.stop()

    st.markdown("### 🔎 Previa (GSC)")
    st.caption(f"Ventana: **{start_date} → {end_date}** · Origen: **{origen}** · Límite/origen: **{max_urls}** · Orden: **{order_by}**")

    def _fetch_for_source(kind_label: str, gsc_type: str) -> pd.DataFrame:
        desc = (order_by != "position")
        if order_by == "position":
            desc = False  # posición conviene ascendente
        rows = _gsc_fetch_top_urls(
            sc_service, site_url, start_date, end_date, gsc_type,
            country or None, device_val, order_by, max_urls, descending=desc
        )
        df = pd.DataFrame(rows)
        if not df.empty:
            df["source"] = kind_label
        return df

    dfs = []
    if origen in ("Search","Search + Discover"):
        dfs.append(_fetch_for_source("Search", "web"))
    if origen in ("Discover","Search + Discover"):
        dfs.append(_fetch_for_source("Discover", "discover"))
    df_seeds = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame(columns=["page","clicks","impressions","ctr","position","source"])

    before = len(df_seeds)
    if before == 0:
        st.warning("No hay datos para esa ventana / configuración.")
    else:
        if 'min_clicks' in locals() and min_clicks > 0:
            df_seeds = df_seeds[df_seeds["clicks"] >= min_clicks]
        if 'min_impr' in locals() and min_impr > 0:
            df_seeds = df_seeds[df_seeds["impressions"] >= min_impr]
        if 'only_articles' in locals() and only_articles:
            df_seeds = df_seeds[df_seeds["page"].astype(str).apply(_looks_like_article)]
        st.write(f"**Semillas elegibles**: {len(df_seeds):,} (antes {before:,})")
        st.code(df_seeds["page"].head(10).tolist())

    urls = df_seeds["page"].astype(str).tolist() if not df_seeds.empty else []
    sources = df_seeds["source"].astype(str).tolist() if not df_seeds.empty else []
    clicks = df_seeds["clicks"].tolist() if not df_seeds.empty else []
    imps   = df_seeds["impressions"].tolist() if not df_seeds.empty else []
    ctrs   = df_seeds["ctr"].tolist() if not df_seeds.empty else []

    # =========================
    # 2) Ejecutar Scraping
    # =========================
    can_run = len(urls) > 0
    if not _HAS_LXML:
        st.info("ℹ️ **lxml no está disponible**. Las extracciones por **XPath** (artículo, H2/H3, negritas, links scoping, tags y relacionadas) "
                "no se podrán calcular. Se extraerán igualmente TITLE / OG title / H1 (si se puede).")

    run = st.button("🚀 Ejecutar extracción y publicar en Sheets", type="primary", disabled=not can_run)

    if run:
        ua = _suggest_user_agent(user_agent_in)
        opts = dict(
            want_title=want_title,
            want_h1=want_h1,
            want_og=want_og,
            h1_selector=h1_selector,
            want_first_p=want_first_p,
            article_xpath=article_xpath or "//article",
            first_p_xpath=first_p_xpath or ".//p[1]",
            want_h2=want_h2,
            want_h3=want_h3,
            want_bold_count=want_bold_count,
            want_bold_list=want_bold_list,
            want_links_count=want_links_count,
            want_links_anchors=want_links_anchors,
            tags_xpath=tags_xpath,
            want_tags_list=want_tags_list,
            want_tags_count=want_tags_count,
            want_related=want_related,
            related_xpath=related_xpath
        )

        results = []
        st.write(f"Descargando y parseando **{len(urls):,}** páginas con concurrencia **{concurrency}**…")
        progress = st.progress(0.0)
        done = 0

        def _worker(u):
            return _scrape_one(u, timeout=timeout, ua=ua, opts=opts)

        with concurrent.futures.ThreadPoolExecutor(max_workers=int(concurrency)) as ex:
            future_to_idx = {ex.submit(_worker, u): i for i, u in enumerate(urls)}
            for fut in concurrent.futures.as_completed(future_to_idx):
                i = future_to_idx[fut]
                try:
                    res = fut.result()
                except Exception as e:
                    res = {"url": urls[i], "_errors": f"{e.__class__.__name__}: {e}"}
                results.append((i, res))
                done += 1
                progress.progress(min(1.0, done/len(urls)))

        # Restaurar orden original
        results.sort(key=lambda x: x[0])
        rows_scraped = [r for _, r in results]

        # =========================
        # 3) Armar DataFrame final
        # =========================
        cols = ["source","url","clicks","impressions","ctr"]

        # Titulación
        if want_title: cols.append("title")
        if want_h1: cols.append("h1")
        if want_og: cols.append("og_title")

        # Artículo
        if want_first_p: cols.append("first_paragraph")

        # H2/H3
        if want_h2: cols.extend(["h2_count","h2_list"])
        if want_h3: cols.extend(["h3_count","h3_list"])

        # Negritas
        if want_bold_count: cols.append("bold_count")
        if want_bold_list: cols.append("bold_list")

        # Links
        if want_links_count: cols.append("link_count")
        if want_links_anchors: cols.append("link_anchors")

        # Tags
        if want_tags_count: cols.append("tags_count")
        if want_tags_list: cols.append("tags_list")

        # Relacionadas
        if want_related:
            if want_related_count: cols.append("related_count")
            if want_related_anchors: cols.append("related_anchors")

        # Siempre útil tener errores de scraping
        cols.append("_errors")

        out_rows = []
        for i, r in enumerate(rows_scraped):
            row = {
                "source": sources[i] if i < len(sources) else "",
                "url": urls[i],
                "clicks": clicks[i] if i < len(clicks) else 0,
                "impressions": imps[i] if i < len(imps) else 0,
                "ctr": ctrs[i] if i < len(ctrs) else 0.0,
                "_errors": r.get("_errors","")
            }
            # Titulación
            if want_title: row["title"] = r.get("title","")
            if want_h1: row["h1"] = r.get("h1","")
            if want_og: row["og_title"] = r.get("og_title","")

            # Artículo
            if want_first_p: row["first_paragraph"] = r.get("first_paragraph","")

            # H2/H3
            if want_h2:
                h2_list = r.get("h2_list",[]) or []
                row["h2_count"] = len(h2_list)
                row["h2_list"] = list_sep.join(h2_list) if h2_list else ""
            if want_h3:
                h3_list = r.get("h3_list",[]) or []
                row["h3_count"] = len(h3_list)
                row["h3_list"] = list_sep.join(h3_list) if h3_list else ""

            # Negritas
            if want_bold_count or want_bold_list:
                b_list = r.get("bold_list",[]) or []
                if want_bold_count: row["bold_count"] = len(b_list)
                if want_bold_list: row["bold_list"] = list_sep.join(b_list) if b_list else ""

            # Links (en artículo)
            if want_links_count or want_links_anchors:
                a_list = r.get("link_anchors",[]) or []
                if want_links_count: row["link_count"] = len(a_list)
                if want_links_anchors: row["link_anchors"] = list_sep.join(a_list) if a_list else ""

            # Tags
            if (want_tags_count or want_tags_list):
                t_list = r.get("tags_list",[]) or []
                if want_tags_count: row["tags_count"] = len(t_list)
                if want_tags_list: row["tags_list"] = list_sep.join(t_list) if t_list else ""

            # Relacionadas
            if want_related:
                rel = r.get("related_anchors",[]) or []
                if want_related_count: row["related_count"] = len(rel)
                if want_related_anchors: row["related_anchors"] = list_sep.join(rel) if rel else ""

            out_rows.append(row)

        df_out = pd.DataFrame(out_rows, columns=cols)

        # =========================
        # 4) Publicar en Sheets
        # =========================
        try:
            # Crear Spreadsheet
            host = _host_from_site(site_url)
            stamp = datetime.now().strftime("%Y%m%d_%H%M")
            title = f"SEO_Extraccion_{host}_{start_date}_{end_date}_{stamp}"
            sh = gs_client.create(title)
            sid = sh.id

            # Mover a carpeta destino (si aplica)
            dest_folder_id = st.session_state.get("dest_folder_id")
            if dest_folder_id:
                # obtener padres actuales
                meta = drive_service.files().get(fileId=sid, fields="parents").execute()
                prev_parents = ",".join(meta.get("parents", []))
                drive_service.files().update(
                    fileId=sid,
                    addParents=dest_folder_id,
                    removeParents=prev_parents,
                    fields="id, parents"
                ).execute()

            ws = sh.sheet1
            ws.resize(1, len(cols))
            ws.update([cols] + df_out.fillna("").values.tolist())

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
                analysis_kind="Extractor rápido (GSC + Scrape)",
                sheet_id=sid, sheet_name=sheet_name, sheet_url=sheet_url,
                gsc_account=st.session_state.get("src_account_label") or "",
                notes=f"rows={len(df_out)}; origen={origen}; filtros=country:{country},device:{device_val}"
            )
            st.session_state["last_file_id"] = sid
            st.session_state["last_file_kind"] = "quick"

        except Exception as e:
            st.error(f"No pude escribir en Sheets: {e}")
            if st.session_state.get("DEBUG"):
                st.exception(e)

# ====== Otras ramas (Core, Evergreen, Auditoría) ======
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
            st.session_state["last_file_id"] = sid
            st.session_state["last_file_kind"] = "audit"

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