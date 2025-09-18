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

# ====== Documento de texto ======
# Intentar local -> externo -> fallback mínimo.
from modules.utils import ensure_external_package
_EXT = ensure_external_package()  # asegura que seo_analisis_ext quede disponible en sys.path
try:
    from modules.doc_export import create_doc_from_template_with_content
except Exception:
    try:
        # Implementación del repo externo (si está instalado/cargado por ensure_external_package)
        from seo_analisis_ext.doc_export import create_doc_from_template_with_content  # type: ignore
    except Exception:
        # Fallback mínimo: crea un Doc en blanco, inserta el texto y lo mueve a la carpeta destino.
        def create_doc_from_template_with_content(credentials, title, analysis_text, dest_folder_id=None):
            from googleapiclient.discovery import build
            drive = build("drive", "v3", credentials=credentials, cache_discovery=False)
            docs  = build("docs",  "v1", credentials=credentials, cache_discovery=False)
            # Crear Doc
            doc = docs.documents().create(body={"title": title}).execute()
            doc_id = doc["documentId"]
            # Mover a carpeta destino (si aplica)
            if dest_folder_id:
                meta = drive.files().get(fileId=doc_id, fields="parents", supportsAllDrives=True).execute()
                prev_parents = ",".join(meta.get("parents", []) or [])
                drive.files().update(
                    fileId=doc_id,
                    addParents=dest_folder_id,
                    removeParents=prev_parents if prev_parents else None,
                    supportsAllDrives=True,
                    fields="id, parents",
                ).execute()
            # Insertar contenido al inicio
            docs.documents().batchUpdate(
                documentId=doc_id,
                body={"requests": [{"insertText": {"location": {"index": 1}, "text": analysis_text}}]},
            ).execute()
            return doc_id

# ====== Carga de módulos ======
from modules.app_config import apply_base_style_and_logo, get_app_home
from modules.app_ext import (
    USING_EXT,
    run_core_update,
    run_evergreen,
    run_traffic_audit,
    run_names_analysis,
    run_discover_snoop,
    run_content_analysis,
    run_content_structure,  # <- NUEVO
    run_discover_retention,  # NUEVO
)

from modules.app_utils import get_qp, clear_qp, has_gsc_scope, norm, has_ga4_scope

def has_docs_scope(scopes: set[str] | list[str] | tuple[str, ...] | None) -> bool:
    return "https://www.googleapis.com/auth/documents" in set(scopes or [])

from modules.app_ai import load_prompts, gemini_healthcheck, gemini_summary
from modules.app_params import (
    params_for_core_update,
    params_for_evergreen,
    params_for_auditoria,
    params_for_names,
)
try:
    from modules.app_params import params_for_discover_snoop
except Exception:
    params_for_discover_snoop = lambda: {}
try:
    from modules.app_params import params_for_content
except Exception:
    params_for_content = None

try:
    from modules.app_ext import run_sections_analysis, run_ga4_audience_report
except Exception:
    try:
        from modules.app_ext import run_sections_analysis
    except Exception:
        run_sections_analysis = None
    run_ga4_audience_report = None

from modules.app_activity import maybe_prefix_sheet_name_with_medio, activity_log_append
from modules.app_errors import run_with_indicator
try:
    from modules.app_auth_flow import step0_google_identity, logout_screen
except ModuleNotFoundError:
    from app_auth_flow import step0_google_identity, logout_screen
except Exception as e:
    import traceback as _tb
    st.error(f"Error al importar modules.app_auth_flow: {e}")
    st.code(_tb.format_exc())
    st.stop()

from modules.app_diagnostics import scan_repo_for_gsc_and_filters, read_context
from modules.utils import token_store
from modules.drive import ensure_drive_clients, get_google_identity, pick_destination, share_controls
from modules.gsc import ensure_sc_client

# ====== Módulos GA4 ======
try:
    from modules.ga4_admin import build_admin_client, list_account_property_summaries
    from modules.ga4_data import build_data_client
except Exception:
    build_admin_client = None
    build_data_client = None
    def list_account_property_summaries(_): return []
try:
    from google.api_core.exceptions import PermissionDenied
except Exception:
    class PermissionDenied(Exception): pass

# ====== Estilo ======
apply_base_style_and_logo()
try:
    apply_page_style()
except Exception:
    pass

st.markdown("""<style>header[data-testid="stHeader"] { z-index:1500 !important; }</style>""", unsafe_allow_html=True)
st.title("Analizador SEO 🚀")

# Flag global
st.session_state.setdefault("post_actions_visible", False)

# ---------- IA / Prompts ----------
load_prompts()
if not st.session_state.get("DEBUG"):
    try:
        ok, _ = gemini_healthcheck()
        if not ok:
            st.caption("💡 Podés cargar una API key de Gemini en Secrets.")
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
    # Pequeño panel de diagnóstico opcional
    if st.session_state.get("DEBUG"):
        try:
            ctx = read_context()
            st.caption(f"Context: {ctx}")
        except Exception:
            pass

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
elif _action == "change_ga4":
    for k in ("creds_ga4","ga4_step_done","ga4_account_label","ga4_property_choice","ga4_property_id","ga4_property_label","ga4_property_name"):
        st.session_state.pop(k, None)
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

    # Verificar scopes sin romper si falta info
    try:
        _scopes_have = set((st.session_state.get("creds_dest") or {}).get("scopes") or [])
        if not has_docs_scope(_scopes_have):
            st.caption(
                "⚠️ Esta sesión no tiene permisos de **Google Docs**. "
                "Si pensás exportar el resumen a Docs, repetí el **Paso 0** con el scope de Docs activo."
            )
    except Exception:
        pass

    # Log de actividad cuando todo lo anterior salió bien
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
        "2. Reporte de resultados",
        "3. Análisis de secciones",          # <- antes decía (🚧 próximamente)
        "4. Análisis de impacto de Core Update",
        "5. Análisis de tráfico evergreen",
    ]
    if include_auditoria:
        opciones.append("6. Auditoría de tráfico")
    if include_names:
        opciones.append("7. Análisis de Nombres (KG + Wikipedia)")
    if include_discover:
        opciones.append("8. Análisis en base a Discover Snoop")
    if include_content:
        opciones.append("9. Análisis de contenido (repo externo)")
    opciones.append("10. Análisis de estructura de contenidos")
    opciones.append("11. Reporte de audiencia (GA4)")  # <-- NUEVO
    opciones.append("12. Incorp. y permanencia en Discover (10 días horarios)")  # <-- NUEVO

    key = st.radio("Tipos disponibles:", opciones, index=1, key="analysis_choice")
    if key.startswith("2."): return "2"
    if key.startswith("3."): return "3"
    if key.startswith("4."): return "4"
    if key.startswith("5."): return "5"
    if key.startswith("6."): return "6"
    if key.startswith("7."): return "7"
    if key.startswith("8."): return "8"
    if key.startswith("9."): return "9"
    if key.startswith("10."): return "10"
    if key.startswith("11."): return "11"   # <-- NUEVO
    if key.startswith("12."): return "12"   # <-- NUEVO
    return "0"

analisis = pick_analysis(include_auditoria, include_names=True, include_discover=True, include_content=True)

# ===== Selector de FUENTES de datos (SC / GA4 / Sin datos) =====
SC_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
GA_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"

def _defaults_for_analysis(a: str) -> tuple[bool, bool, bool]:
    """
    Devuelve (def_SC, def_GA, def_NONE) según el tipo de análisis.
    """
    # a es "2", "3", ..., "10"
    if a == "2":   # Reporte de resultados → ideal ambos, pero sirve con uno (por ahora requiere SC para este build)
        return True, True, False
    if a in ("3","4","5"):  # Secciones/Core/Evergreen → típicamente SC
        return True, False, False
    if a == "6":   # Auditoría de tráfico → GA + SC (este build usa SC)
        return True, True, False
    if a == "7":   # Nombres → sin datos (opcional GA)
        return False, False, True
    if a == "8":   # Discover Snoop → normalmente sin datos (opcional SC)
        return False, False, True
    if a == "9":   # Análisis de contenido (runner externo)
        return True, False, False  # SC por defecto
    if a == "10":  # Estructura de contenidos → suele usar SC
        return True, False, False
    if a == "11":  # Reporte de audiencia (GA4)
        return False, True, False
    if a == "12":  # Incorp. y permanencia Discover
        return True, False, False
    # Futuro/otros
    return False, False, True

st.subheader("Fuentes de datos para este análisis")
def_sc, def_ga, def_none = _defaults_for_analysis(analisis)
use_sc = st.checkbox("Usar Search Console", value=def_sc, key=f"ds_sc_{analisis}")
use_ga = st.checkbox("Usar Google Analytics 4", value=def_ga, key=f"ds_ga_{analisis}")
use_none = st.checkbox("Sin datos del cliente", value=def_none, key=f"ds_none_{analisis}")

# Normalizar selección (si no marcó nada, forzamos 'Sin datos')
if not any([use_sc, use_ga, use_none]):
    st.info("Seleccioná al menos una fuente (SC, GA4 o 'Sin datos del cliente').")
    use_none = True

selected_sources = {s for s, v in (("sc", use_sc), ("ga", use_ga), ("none", use_none)) if v}
st.session_state["selected_sources"] = selected_sources

# ===== Cuenta de acceso única (se usa para SC y/o GA4 según lo marcado) =====
need_auth = any(s in selected_sources for s in ("sc", "ga"))
sc_service = None
ga4_admin = None
ga4_data = None

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

def _pick_source_oauth_forced(account_key: str, scopes: list[str]) -> Credentials | None:
    key = f"oauth_src_{account_key}_{'_'.join(sorted(scopes))}"
    if key not in st.session_state:
        flow = _build_flow_installed_or_local(account_key, scopes)
        auth_url, state = flow.authorization_url(prompt="consent select_account", access_type="offline")
        st.session_state[key] = {"account": account_key, "flow": flow, "auth_url": auth_url, "state": state}

    osrc = st.session_state[key]
    st.markdown(f"🔗 **Autorizar acceso** (SC/GA4 según selección) → {osrc['auth_url']}")
    with st.expander("Ver/copiar URL de autorización"):
        st.code(osrc["auth_url"])

    url = st.text_input(
        "Pegá la URL de redirección (http://localhost/?code=...&state=...)",
        key=f"auth_response_url_src_{account_key}",
        placeholder="http://localhost/?code=...&state=...",
    )

    c1, c2 = st.columns([1,1])
    creds = None
    with c1:
        if st.button("Conectar", key=f"btn_connect_src_{account_key}", type="secondary"):
            if not (url or "").strip():
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
                st.error("No se pudo completar la autorización. Reintentá.")
                st.caption(f"Detalle técnico: {e}")
    with c2:
        if st.button("Reiniciar conexión", key=f"btn_reset_src_{account_key}"):
            st.session_state.pop(key, None)
            for k in ("creds_src","step3_done","src_account_label"):
                st.session_state.pop(k, None)
            clear_qp(); st.rerun()
    return creds

if need_auth:
    st.subheader("Seleccioná la cuenta de acceso (se usará para SC y/o GA4)")
    account_options = ["Acceso", "Acceso Medios", "Acceso en cuenta personal de Nomadic"]
    _default_label = st.session_state.get("sc_account_choice", "Acceso en cuenta personal de Nomadic")
    default_idx = account_options.index(_default_label) if _default_label in account_options else 2
    sc_choice = st.selectbox(
        "Elegí la cuenta para consultar datos",
        account_options, index=default_idx, key="sc_account_choice"
    )

    def _choice_to_key(label: str) -> str | None:
        if norm(label) == norm("Acceso"): return "ACCESO"
        if norm(label) == norm("Acceso Medios"): return "ACCESO_MEDIOS"
        return None  # personal usa creds_dest

    # Scopes dinámicos según selección
    wanted_scopes: list[str] = []
    if "sc" in selected_sources:
        wanted_scopes.append(SC_SCOPE)
    if "ga" in selected_sources:
        wanted_scopes.append(GA_SCOPE)

    # Inicializar clientes según elección
    if sc_choice == "Acceso en cuenta personal de Nomadic":
        creds_dest_dict = st.session_state.get("creds_dest") or token_store.load("creds_dest")
        if not creds_dest_dict:
            st.error("No encuentro la sesión personal. Volvé a iniciar sesión en el Paso 0."); st.stop()

        # Validar scopes en la sesión personal
        scopes_have = set(creds_dest_dict.get("scopes") or [])
        missing = []
        if "sc" in selected_sources and not has_gsc_scope(scopes_have): missing.append("Search Console")
        if "ga" in selected_sources and not has_ga4_scope(scopes_have): missing.append("Google Analytics (analytics.readonly)")

        if missing:
            st.warning("Tu cuenta personal no tiene los permisos requeridos: " + ", ".join(missing))
            st.caption("Volvé a realizar el Paso 0 solicitando también esos permisos.")
            st.stop()

        try:
            creds_src = Credentials(**creds_dest_dict)
            if "sc" in selected_sources:
                sc_service = ensure_sc_client(creds_src)
            if "ga" in selected_sources:
                if build_admin_client is None or build_data_client is None:
                    raise RuntimeError("Módulos GA4 no disponibles. Agregá `modules/ga4_admin.py`, `modules/ga4_data.py` y paquetes `google-analytics-*` a requirements.")
                ga4_admin = build_admin_client(creds_src)
                ga4_data = build_data_client(creds_src)

            st.session_state["creds_src"] = creds_dest_dict
            st.session_state["src_account_label"] = "Acceso en cuenta personal de Nomadic"
            st.session_state["step3_done"] = True
            st.markdown(
                f'''
                <div class="success-inline">
                    Cuenta de acceso: <strong>Acceso en cuenta personal de Nomadic</strong>
                    <a href="{APP_HOME}?action=change_src" target="_self" rel="nofollow">(Cambiar cuenta de acceso)</a>
                </div>
                ''',
                unsafe_allow_html=True
            )
        except Exception as e:
            st.error(f"No pude inicializar clientes con la cuenta personal: {e}")
            st.stop()

    else:
        wanted_key = _choice_to_key(sc_choice)  # "ACCESO" o "ACCESO_MEDIOS"
        need_new_auth = (
            not st.session_state.get("step3_done") or
            norm(st.session_state.get("src_account_label")) != norm(sc_choice) or
            norm(st.session_state.get("src_account_label")) == norm("Acceso en cuenta personal de Nomadic")
        )
        if need_new_auth:
            creds_src_obj = _pick_source_oauth_forced(wanted_key, wanted_scopes)
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
                if "sc" in selected_sources:
                    sc_service = ensure_sc_client(creds_src)
                if "ga" in selected_sources:
                    if build_admin_client is None or build_data_client is None:
                        raise RuntimeError("Módulos GA4 no disponibles. Agregá `modules/ga4_admin.py`, `modules/ga4_data.py` y paquetes `google-analytics-*` a requirements.")
                    ga4_admin = build_admin_client(creds_src)
                    ga4_data = build_data_client(creds_src)
                src_label = st.session_state.get("src_account_label") or sc_choice
                st.markdown(
                    f'''
                    <div class="success-inline">
                        Cuenta de acceso: <strong>{src_label}</strong>
                        <a href="{APP_HOME}?action=change_src" target="_self" rel="nofollow">(Cambiar cuenta de acceso)</a>
                    </div>
                    ''',
                    unsafe_allow_html=True
                )
            except Exception as e:
                st.error(f"No pude inicializar clientes: {e}")
                st.stop()

# ===== Selector de propiedad GA4 (si corresponde) =====
if "ga" in selected_sources:
    st.subheader("Seleccioná la propiedad de Google Analytics 4")
    props = []
    _perm_issue = False
    try:
        props = list_account_property_summaries(ga4_admin)
    except PermissionError as e:
        if str(e) == "GA4_ADMIN_PERMISSION":
            _perm_issue = True
        else:
            st.error(f"GA4 Admin API: {e}")
    except PermissionDenied:
        _perm_issue = True
    except Exception as e:
        st.error(f"GA4 Admin API (otro error): {e}")

    if _perm_issue:
        st.warning(
            "No pude listar cuentas/propiedades con la **Analytics Admin API**. "
            "Ingresá el **ID de la propiedad GA4** manualmente."
        )
        pid_manual = st.text_input(
            "ID de propiedad GA4 (número, ej. 123456789)",
            value=st.session_state.get("ga4_property_id", ""),
            key="ga4_pid_manual"
        ).strip()
        if pid_manual:
            st.session_state["ga4_property_id"] = pid_manual
            props = [{
                "account_name": "—",
                "property_display_name": f"Propiedad {pid_manual} (manual)",
                "property_id": pid_manual
            }]

    if props:
        labels = [
            f"{p['property_display_name']} — {p.get('account_name','—')} (ID {p['property_id']})"
            for p in props
        ]
        default_idx = 0
        if "ga4_property_id" in st.session_state:
            try:
                default_idx = next(
                    (i for i, p in enumerate(props) if p["property_id"] == st.session_state["ga4_property_id"]),
                    0
                )
            except Exception:
                default_idx = 0
        choice = st.selectbox("Propiedad GA4", labels, index=default_idx, key="ga4_property_choice")
        sel = props[labels.index(choice)]
        st.session_state["ga4_property_id"] = sel["property_id"]
        st.session_state["ga4_property_name"] = sel["property_display_name"]
        st.markdown(
            f"""
            <div class="success-inline">
                Propiedad GA4 seleccionada: <strong>{st.session_state['ga4_property_name']} (ID {st.session_state['ga4_property_id']})</strong>
                <a href="{APP_HOME}?action=change_ga4" target="_self" rel="nofollow">(Cambiar propiedad GA4)</a>
            </div>
            """,
            unsafe_allow_html=True
        )
    else:
        st.info("No hay propiedades para mostrar todavía.")

# ===== Sitios de GSC (solo si se seleccionó SC) =====
if "sc" in selected_sources:
    def pick_sites(sc_service) -> list[str]:
        st.subheader("Elige el/los sitios a analizar (Search Console)")
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
        multi = st.toggle("Analizar múltiples sitios", value=False, key="multi_sites")

        if multi:
            prev_multi = st.session_state.get("site_urls_choice", [])
            defaults = prev_multi or ([st.session_state.get("site_url_choice")] if st.session_state.get("site_url_choice") in options else [])
            selected = st.multiselect("Sitios verificados:", options, default=[x for x in defaults if x in options], key="site_urls_choice")
            return selected
        else:
            prev = st.session_state.get("site_url_choice")
            index = options.index(prev) if prev in options else 0
            site = st.selectbox("Sitio verificado:", options, index=index, key="site_url_choice")
            return [site]

    site_urls = pick_sites(sc_service)
    site_url = site_urls[0] if site_urls else None
else:
    # Sin SC → mantener compatibilidad con el resto del código
    site_urls = []
    site_url = None

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

# === Helper multi-sitio para runners GSC
def run_for_sites(titulo: str, fn, sc_service, drive_service, gs_client, site_urls: list[str], params: dict, dest_folder_id: str | None):
    created: list[tuple[str, str]] = []
    n = len(site_urls)
    prog = st.progress(0.0)
    for i, s in enumerate(site_urls, 1):
        sid = run_with_indicator(f"{titulo} — {s}", fn, sc_service, drive_service, gs_client, s, params, dest_folder_id)
        if sid:
            try:
                maybe_prefix_sheet_name_with_medio(drive_service, sid, s)
            except Exception:
                pass
            created.append((s, sid))
        prog.progress(i / n)
    prog.empty()
    return created

# ===== spaCy bootstrap (modelo autoinstalable sin permisos en site-packages) =====
@st.cache_resource(show_spinner=False)
def ensure_spacy(preferred_models=("es_core_news_sm","xx_ent_wiki_sm","en_core_web_sm"),
                 local_dirs=("models/es_core_news_sm","models/xx_ent_wiki_sm","models/en_core_web_sm")):
    """
    Devuelve (nlp, model_id, how):
      nlp: objeto spaCy Language
      model_id: nombre o ruta del modelo cargado
      how: 'package' | 'path' | 'downloaded'
    Estrategia:
      1) Cargar paquete instalado (import + spacy.load)
      2) Cargar desde carpeta local (repo) o SPACY_MODEL_DIR
      3) Descargar wheel del modelo a un directorio temporal con --target y cargar
    """
    import os, sys, importlib, tempfile, subprocess
    try:
        import spacy
    except Exception as e:
        raise RuntimeError("spaCy no está instalado. Agrega 'spacy>=3.8,<3.9' a requirements.txt") from e

    # 1) Paquetes instalados
    for name in preferred_models:
        try:
            return spacy.load(name), name, "package"
        except Exception:
            pass

    # 2) Carpetas locales (por si incluyes el modelo en el repo)
    search_paths = []
    if os.environ.get("SPACY_MODEL_DIR"):
        search_paths.append(os.environ["SPACY_MODEL_DIR"])
    search_paths.extend(local_dirs)
    for p in search_paths:
        if not p:
            continue
        p_abs = os.path.abspath(p)
        if os.path.isdir(p_abs):
            try:
                return spacy.load(p_abs), p_abs, "path"
            except Exception:
                pass

    # 3) Descargar a un directorio temporal (sin tocar site-packages)
    wheels = {
        "es_core_news_sm": "https://github.com/explosion/spacy-models/releases/download/es_core_news_sm-3.8.0/es_core_news_sm-3.8.0-py3-none-any.whl",
        "xx_ent_wiki_sm": "https://github.com/explosion/spacy-models/releases/download/xx_ent_wiki_sm-3.8.0/xx_ent_wiki_sm-3.8.0-py3-none-any.whl",
        "en_core_web_sm": "https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl",
    }
    target = os.path.join(tempfile.gettempdir(), "spacy_models")
    os.makedirs(target, exist_ok=True)
    last_err = ""
    for name in preferred_models:
        url = wheels.get(name)
        if not url:
            continue
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "--no-cache-dir", "--target", target, url])
            if target not in sys.path:
                sys.path.insert(0, target)
            pkg = importlib.import_module(name)
            try:
                nlp = pkg.load()
            except Exception:
                import spacy as _sp
                nlp = _sp.load(name)
            return nlp, name, "downloaded"
        except Exception as e:
            last_err = str(e)
            continue
    raise RuntimeError(f"No pude cargar ni instalar modelos spaCy. Intentos: {preferred_models}. Último error: {last_err}")

# -------------------------
# Scraping rápido (async) + parsing
# -------------------------
def _parse_html_for_meta(html: str, wants: dict, xpaths: dict, joiner: str = " | ") -> dict:
    """
    Extrae campos en función de 'wants' (dict de booleans) y 'xpaths' (opcional).
    Campos soportados:
      h1, title, meta_description, og_title, og_description, canonical, published_time, lang,
      first_paragraph, article_text,
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
        "first_paragraph": "", "article_text": "",
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

    # BeautifulSoup
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

    # Determinar contenedor del artículo (scope)
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

    # Texto completo del artículo (opcional, para entidades)
    if wants.get("article_text"):
        text_all = ""
        if have_lxml and lxml_scope_nodes:
            try:
                chunks = []
                for node in lxml_scope_nodes:
                    try:
                        t = node.xpath("string(.)")
                        if t and t.strip():
                            chunks.append(t.strip())
                    except Exception:
                        pass
                text_all = "\n".join(chunks).strip()
            except Exception:
                text_all = ""
        if not text_all and soup_scope:
            try:
                text_all = soup_scope.get_text(" ", strip=True)
            except Exception:
                text_all = ""
        data["article_text"] = text_all

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

# ============== Flujos por análisis ==============

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

# ============== Resto de análisis (algunos requieren SC) ==============

# Análisis que requieren sí o sí Search Console:
def _require_sc_or_stop():
    if "sc" not in (st.session_state.get("selected_sources") or set()):
        st.warning("Este análisis requiere datos de **Search Console**. Marcá *Usar Search Console* arriba.")
        st.stop()
    if not sc_service:
        st.error("El cliente de Search Console no está inicializado."); st.stop()
    if not site_urls:
        st.error("No hay sitios seleccionados de Search Console."); st.stop()

# Guardrail para GA4
def _require_ga_or_stop():
    if "ga" not in (st.session_state.get("selected_sources") or set()):
        st.warning("Este análisis requiere datos de **Google Analytics 4**. Marcá *Usar Google Analytics 4* arriba.")
        st.stop()
    if not ga4_data:
        st.error("El cliente de GA4 (Data API) no está inicializado."); st.stop()
    if not st.session_state.get("ga4_property_id"):
        st.error("No hay una **propiedad GA4** seleccionada."); st.stop()

if analisis == "4":
    _require_sc_or_stop()
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

            if len(site_urls) <= 1:
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
                st.session_state["post_actions_visible"] = True
            else:
                results = run_for_sites("Procesando Core Update", run_core_update,
                                        sc_service, drive_service, gs_client, site_urls, params, st.session_state.get("dest_folder_id"))
                st.success(f"¡Listo! Se generaron {len(results)} documentos.")
                for s, sid in results:
                    st.markdown(f"• **{s}** → https://docs.google.com/spreadsheets/d/{sid}")
                    activity_log_append(
                        drive_service, gs_client,
                        user_email=( _me or {}).get("emailAddress") or "",
                        event="analysis", site_url=s,
                        analysis_kind="Core Update",
                        sheet_id=sid, sheet_name="", sheet_url=f"https://docs.google.com/spreadsheets/d/{sid}",
                        gsc_account=st.session_state.get("src_account_label") or "",
                        notes=f"params={params!r}"
                    )
                if results:
                    st.session_state["last_file_id"] = results[-1][1]
                    st.session_state["last_file_kind"] = "core"
                    st.session_state["post_actions_visible"] = True

elif analisis == "3":
    _require_sc_or_stop()
    if run_sections_analysis is None:
        st.warning("Este despliegue no incluye `run_sections_analysis` (agregá `seo_analisis_ext/sections_analysis.py`).")
        st.stop()

    st.subheader("Análisis de secciones")
    st.caption("Compara secciones y subsecciones por períodos consecutivos (semanal, quincenal, mensual o personalizado), para Search y/o Discover.")

    # ------- Parámetros -------
    lag = st.number_input("Lag de días (para evitar datos incompletos)", 0, 10, 3, 1, key="sec_lag")

    period_choice = st.radio(
        "Periodo",
        ["Semanal", "Quincenal", "Mensual", "Personalizado"],
        index=0, horizontal=True, key="sec_period"
    )

    n_prev = st.number_input("¿Cuántos períodos previos comparar?", 0, 24, 4, 1, key="sec_nprev")

    # Personalizado
    custom_start = None; custom_end = None
    if period_choice == "Personalizado":
        c1, c2 = st.columns(2)
        with c1: custom_start = st.date_input("Desde (inclusive)", key="sec_custom_from")
        with c2: custom_end   = st.date_input("Hasta (inclusive)", key="sec_custom_to")

    # Origen
    origin_label = st.radio("Origen", ["Search", "Discover", "Search y Discover"], index=0, horizontal=True, key="sec_origin")
    origin_map = {"Search": "search", "Discover": "discover", "Search y Discover": "both"}
    origin = origin_map[origin_label]

    # (Opcionales) Filtros
    with st.expander("Filtros opcionales"):
        path = st.text_input("Filtrar por sección (path contiene)", value="", key="sec_path") or None
        country = st.text_input("País (ISO-3, ej: ARG/ESP/USA)", value="", key="sec_country") or None

    # Métricas a incluir (pos solo afecta a Search)
    st.markdown("**Métricas a incluir en Detalle**")
    mc1, mc2, mc3, mc4 = st.columns(4)
    with mc1: m_clicks = st.checkbox("Clics", value=True, key="sec_m_clicks")
    with mc2: m_impr   = st.checkbox("Impresiones", value=True, key="sec_m_impr")
    with mc3: m_ctr    = st.checkbox("CTR", value=True, key="sec_m_ctr")
    with mc4:
        disable_pos = (origin == "discover")
        m_pos = st.checkbox("Posición (solo Search)", value=(origin != "discover"), key="sec_m_pos", disabled=disable_pos)

    # Mapear modo
    mode_map = {"Semanal":"weekly","Quincenal":"biweekly","Mensual":"monthly","Personalizado":"custom"}
    mode = mode_map[period_choice]

    params = {
        "period_mode": mode,
        "lag_days": int(lag),
        "n_prev": int(n_prev),
        "origin": origin,
        "metrics": {"clicks": bool(m_clicks), "impressions": bool(m_impr), "ctr": bool(m_ctr), "position": bool(m_pos)},
        "path": path,
        "country_iso3": country,
    }
    if mode == "custom":
        params["custom_start"] = custom_start
        params["custom_end"] = custom_end

    can_run = (mode != "custom") or (custom_start and custom_end and custom_start <= custom_end)
    if st.button("🧭 Ejecutar Análisis de secciones", type="primary", disabled=not can_run, key="sec_run"):
        if len(site_urls) <= 1:
            sid = run_with_indicator(
                "Procesando Secciones",
                run_sections_analysis, sc_service, drive_service, gs_client, site_url, params,
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
                analysis_kind="Secciones",
                sheet_id=sid, sheet_name=sheet_name, sheet_url=sheet_url,
                gsc_account=st.session_state.get("src_account_label") or "",
                notes=f"mode={mode}, n_prev={int(n_prev)}, lag={int(lag)}, origin={origin}, path={path or 'site'}, country={country or 'GLOBAL'}"
            )
            st.session_state["last_file_id"] = sid
            st.session_state["last_file_kind"] = "sections"
            st.session_state["post_actions_visible"] = True
        else:
            results = run_for_sites("Procesando Secciones", run_sections_analysis,
                                    sc_service, drive_service, gs_client, site_urls, params, st.session_state.get("dest_folder_id"))
            st.success(f"¡Listo! Se generaron {len(results)} documentos.")
            for s, sid in results:
                st.markdown(f"• **{s}** → https://docs.google.com/spreadsheets/d/{sid}")
                activity_log_append(
                    drive_service, gs_client,
                    user_email=( _me or {}).get("emailAddress") or "",
                    event="analysis", site_url=s,
                    analysis_kind="Secciones",
                    sheet_id=sid, sheet_name="", sheet_url=f"https://docs.google.com/spreadsheets/d/{sid}",
                    gsc_account=st.session_state.get("src_account_label") or "",
                    notes=f"mode={mode}, n_prev={int(n_prev)}, lag={int(lag)}, origin={origin}, path={path or 'site'}, country={country or 'GLOBAL'}"
                )
            if results:
                st.session_state["last_file_id"] = results[-1][1]
                st.session_state["last_file_kind"] = "sections"
                st.session_state["post_actions_visible"] = True

elif analisis == "5":
    _require_sc_or_stop()
    if run_evergreen is None:
        st.warning("Este despliegue no incluye run_evergreen.")
    else:
        params = params_for_evergreen()
        if st.button("🌲 Ejecutar análisis Evergreen", type="primary", key="btn_ev_run"):
            if len(site_urls) <= 1:
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
                st.session_state["post_actions_visible"] = True
            else:
                results = run_for_sites("Procesando Evergreen", run_evergreen,
                                        sc_service, drive_service, gs_client, site_urls, params, st.session_state.get("dest_folder_id"))
                st.success(f"¡Listo! Se generaron {len(results)} documentos.")
                for s, sid in results:
                    st.markdown(f"• **{s}** → https://docs.google.com/spreadsheets/d/{sid}")
                    activity_log_append(
                        drive_service, gs_client,
                        user_email=( _me or {}).get("emailAddress") or "",
                        event="analysis", site_url=s,
                        analysis_kind="Evergreen",
                        sheet_id=sid, sheet_name="", sheet_url=f"https://docs.google.com/spreadsheets/d/{sid}",
                        gsc_account=st.session_state.get("src_account_label") or "",
                        notes=f"params={params!r}"
                    )
                if results:
                    st.session_state["last_file_id"] = results[-1][1]
                    st.session_state["last_file_kind"] = "evergreen"
                    st.session_state["post_actions_visible"] = True

elif analisis == "6":
    _require_sc_or_stop()
    if run_traffic_audit is None:
        st.warning("Este despliegue no incluye run_traffic_audit.")
    else:
        params = params_for_auditoria()
        if st.button("🧮 Ejecutar Auditoría de tráfico", type="primary", key="btn_aud_run"):
            if len(site_urls) <= 1:
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
                st.session_state["post_actions_visible"] = True
            else:
                results = run_for_sites("Procesando Auditoría", run_traffic_audit,
                                        sc_service, drive_service, gs_client, site_urls, params, st.session_state.get("dest_folder_id"))
                st.success(f"¡Listo! Se generaron {len(results)} documentos.")
                for s, sid in results:
                    st.markdown(f"• **{s}** → https://docs.google.com/spreadsheets/d/{sid}")
                    activity_log_append(
                        drive_service, gs_client,
                        user_email=( _me or {}).get("emailAddress") or "",
                        event="analysis", site_url=s,
                        analysis_kind="Auditoría",
                        sheet_id=sid, sheet_name="", sheet_url=f"https://docs.google.com/spreadsheets/d/{sid}",
                        gsc_account=st.session_state.get("src_account_label") or "",
                        notes=f"params={params!r}"
                    )
                if results:
                    st.session_state["last_file_id"] = results[-1][1]
                    st.session_state["last_file_kind"] = "audit"
                    st.session_state["post_actions_visible"] = True

elif analisis == "2":
    # Por ahora, este runner usa SC para extraer series.
    _require_sc_or_stop()
    from modules.app_ext import run_report_results
    if run_report_results is None:
        st.warning("Este despliegue no incluye `run_report_results` (agregá `seo_analisis_ext/report_results.py`).")
        st.stop()

    st.subheader("Reporte de resultados")
    st.caption("Extrae series por fecha desde Search Console, por origen (Search/Discover) y opcionalmente por países y sección.")

    # ---------- Periodo ----------
    from datetime import date, timedelta

    def first_day_of_month(d: date) -> date:
        return d.replace(day=1)

    def last_day_of_month(d: date) -> date:
        nd = (d.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        return nd

    def add_months(d: date, months: int) -> date:
        # sin dateutil: cálculo robusto
        y = d.year + (d.month - 1 + months) // 12
        m = (d.month - 1 + months) % 12 + 1
        day = min(d.day, [31,29 if y%4==0 and (y%100!=0 or y%400==0) else 28,31,30,31,30,31,31,30,31,30,31][m-1])
        return date(y, m, day)

    today = date.today()
    lag = st.number_input("Lag de días (para evitar datos incompletos)", 0, 10, 3, 1, key="rep_lag")

    period_choice = st.radio(
        "Periodo",
        ["Semanal", "Quincenal", "Últimos 30 días", "Último mes completo", "Último trimestre completo", "Último año completo", "Últimos 16 meses", "Personalizado"],
        index=0, horizontal=True, key="rep_period"
    )

    anchor = today - timedelta(days=int(lag))
    if period_choice == "Semanal":
        end_dt = anchor
        start_dt = end_dt - timedelta(days=7)
    elif period_choice == "Quincenal":
        end_dt = anchor
        start_dt = end_dt - timedelta(days=15)
    elif period_choice == "Últimos 30 días":
        end_dt = anchor
        start_dt = end_dt - timedelta(days=30)
    elif period_choice == "Último mes completo":
        last_full_end = first_day_of_month(anchor) - timedelta(days=1)
        start_dt = first_day_of_month(last_full_end)
        end_dt = last_full_end
    elif period_choice == "Último trimestre completo":
        # trimestre completo anterior al anchor
        m0 = first_day_of_month(anchor)
        prev_month = m0 - timedelta(days=1)
        q = (prev_month.month - 1) // 3  # 0..3
        q_start_month = q * 3 + 1
        q_start = date(prev_month.year, q_start_month, 1)
        q_end = last_day_of_month(add_months(q_start, 2))
        start_dt, end_dt = q_start, q_end
    elif period_choice == "Último año completo":
        y = (anchor.year - 1) if anchor.month == 1 and anchor.day == 1 else (anchor.year - 1)
        start_dt = date(y, 1, 1)
        end_dt = date(y, 12, 31)
    elif period_choice == "Últimos 16 meses":
        # Meses completos en ambos extremos, usando el día de corte (= anchor.day).
        # 1) Último mes completo = el mes anterior al anchor.
        last_full_end = last_day_of_month(first_day_of_month(anchor) - timedelta(days=1))

        # 2) Punto de corte inicial: (primer día del mes del anchor - 16 meses) + (anchor.day - 1).
        #    Ej.: si anchor es 3/sep, el corte es 3/may; el primer mes CALENDARIO completo que
        #    empieza >= ese corte es JUNIO (no mayo).
        def add_months_local(d: date, months: int) -> date:
            y = d.year + (d.month - 1 + months) // 12
            m = (d.month - 1 + months) % 12 + 1
            day = min(d.day, [31,29 if y%4==0 and (y%100!=0 or y%400==0) else 28,31,30,31,30,31,31,30,31,30,31][m-1])
            return date(y, m, day)
        cut = add_months_local(first_day_of_month(anchor), -16) + timedelta(days=anchor.day - 1)

        start_month = first_day_of_month(cut) if cut.day == 1 else first_day_of_month(add_months_local(cut, 1))
        start_dt, end_dt = start_month, last_full_end
    else:
        # Personalizado
        end_dt = anchor
        start_dt = anchor - timedelta(days=30)

    c1, c2 = st.columns(2)
    with c1:
        start_date = st.date_input("Fecha inicio", value=start_dt, key="rep_start")
    with c2:
        end_date = st.date_input("Fecha fin (inclusive)", value=end_dt, key="rep_end")

    # ---------- Origen ----------
    origin_label = st.radio("Origen", ["Search", "Discover", "Search y Discover"], index=0, horizontal=True, key="rep_origin")
    origin_map = {"Search": "search", "Discover": "discover", "Search y Discover": "both"}
    origin = origin_map[origin_label]

    # ---------- Sección ----------
    path = st.text_input("Sección (path, ej: /vida/) — vacío = todo el sitio", value="", key="rep_path")
    if path and not path.startswith("/"):
        path = "/" + path

    # ---------- Región ----------
    region_mode = st.radio("Región", ["Global", "Países (ISO-3)"], index=0, horizontal=True, key="rep_region_mode")

    ISO3_OPTIONS = [
        "ARG","URY","PRY","BOL","CHL","PER","COL","ECU","VEN","MEX","CRI","PAN","DOM",
        "ESP","PRT","USA","CAN","GBR","FRA","DEU","ITA","NLD","BEL","SWE","NOR","DNK","IRL","CHE","AUT",
        "BRA"
    ]
    countries = []
    if region_mode.startswith("Países"):
        countries = st.multiselect("Elige países (ISO-3)", ISO3_OPTIONS, default=["ARG"], key="rep_countries")
        extra = st.text_input("Códigos adicionales separados por coma (opcional)", value="", key="rep_countries_extra")
        if extra.strip():
            countries += [c.strip().upper() for c in extra.split(",") if c.strip()]

    # ---------- Métricas ----------
    st.markdown("**Métricas a incluir**")
    mc1, mc2, mc3, mc4 = st.columns(4)
    with mc1: m_clicks = st.checkbox("Clics", value=True, key="rep_m_clicks")
    with mc2: m_impr = st.checkbox("Impresiones", value=True, key="rep_m_impr")
    with mc3: m_ctr = st.checkbox("CTR", value=True, key="rep_m_ctr")
    with mc4:
        disable_pos = (origin == "discover")
        m_pos = st.checkbox("Posición (solo Search)", value=True and not disable_pos, key="rep_m_pos", disabled=disable_pos)

    # ---------- Top de notas ----------
    tc1, tc2 = st.columns([1, 2])
    with tc1:
        top_n = st.number_input(
            "Top de notas más vistas",
            min_value=0, max_value=500, value=20, step=5, key="rep_top_n"
        )
    with tc2:
        st.caption("0 = no crear hojas de Top. Se ordena por *Clics* en GSC. "
                   "Si elegís países, se crea una hoja Top por cada país y otra Global.")

    can_run = (start_date <= end_date)
    if st.button("📈 Generar Reporte de resultados", type="primary", disabled=not can_run, key="rep_run"):
        params = {
            "start": start_date,
            "end": end_date,
            "origin": origin,
            "path": path or None,
            "countries": countries or [],
            "metrics": {
                "clicks": bool(m_clicks),
                "impressions": bool(m_impr),
                "ctr": bool(m_ctr),
                "position": bool(m_pos),
            },
            "lag_days": int(lag),
            "sheet_title_prefix": "Reporte de resultados",
            "top_n": int(top_n),
        }
        try:
            if len(site_urls) <= 1:
                sid = run_with_indicator(
                    "Generando Reporte de resultados",
                    run_report_results,  # runner externo
                    sc_service, drive_service, gs_client, site_url, params, st.session_state.get("dest_folder_id")
                )
                if sid:
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
                        analysis_kind="Reporte de resultados",
                        sheet_id=sid, sheet_name=sheet_name, sheet_url=sheet_url,
                        gsc_account=st.session_state.get("src_account_label") or "",
                        notes=f"periodo={start_date}->{end_date}, origin={origin}, path={path or 'site'}, countries={countries}, top_n={int(top_n)}"
                    )
                    st.session_state["last_file_id"] = sid
                    st.session_state["last_file_kind"] = "report_results"
                    st.session_state["post_actions_visible"] = True
            else:
                results = run_for_sites("Generando Reporte de resultados", run_report_results,
                                        sc_service, drive_service, gs_client, site_urls, params, st.session_state.get("dest_folder_id"))
                st.success(f"¡Listo! Se generaron {len(results)} documentos.")
                for s, sid in results:
                    st.markdown(f"• **{s}** → https://docs.google.com/spreadsheets/d/{sid}")
                    activity_log_append(
                        drive_service, gs_client,
                        user_email=( _me or {}).get("emailAddress") or "",
                        event="analysis", site_url=s,
                        analysis_kind="Reporte de resultados",
                        sheet_id=sid, sheet_name="", sheet_url=f"https://docs.google.com/spreadsheets/d/{sid}",
                        gsc_account=st.session_state.get("src_account_label") or "",
                        notes=f"periodo={start_date}->{end_date}, origin={origin}, path={path or 'site'}, countries={countries}, top_n={int(top_n)}"
                    )
                if results:
                    st.session_state["last_file_id"] = results[-1][1]
                    st.session_state["last_file_kind"] = "report_results"
                    st.session_state["post_actions_visible"] = True
        except Exception as e:
            st.error(f"Falló la generación del reporte: {e}")

elif analisis == "11":
    # ===== NUEVO: Reporte de audiencia (GA4) =====
    _require_ga_or_stop()
    if run_ga4_audience_report is None:
        st.warning("Este despliegue no incluye `run_ga4_audience_report` (agregá el módulo en el repo externo o `modules/ga4_audience.py`).")
        st.stop()

    st.subheader("Reporte de audiencia (GA4)")
    st.caption("Usuarios, sesiones y engagement por período, con series temporales y desgloses (canal, dispositivo, país, etc.).")

    # ---------- Período ----------
    today = date.today()
    lag = st.number_input("Lag de días (evitar datos incompletos del día actual)", 0, 3, 0, 1, key="ga4aud_lag")

    period_choice = st.radio(
        "Periodo",
        ["Últimos 28 días", "Último mes completo", "Últimos 90 días", "Personalizado"],
        index=0, horizontal=True, key="ga4aud_period"
    )

    def first_day_of_month(d: date) -> date: return d.replace(day=1)
    def last_day_of_month(d: date) -> date:
        nd = (d.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        return nd

    anchor = today - timedelta(days=int(lag))
    if period_choice == "Últimos 28 días":
        end_dt = anchor
        start_dt = end_dt - timedelta(days=27)
    elif period_choice == "Último mes completo":
        last_full_end = first_day_of_month(anchor) - timedelta(days=1)
        start_dt = first_day_of_month(last_full_end)
        end_dt = last_full_end
    elif period_choice == "Últimos 90 días":
        end_dt = anchor
        start_dt = end_dt - timedelta(days=89)
    else:
        end_dt = anchor
        start_dt = anchor - timedelta(days=27)

    c1, c2 = st.columns(2)
    with c1:
        start_date = st.date_input("Fecha inicio", value=start_dt, key="ga4aud_start")
    with c2:
        end_date = st.date_input("Fecha fin (inclusive)", value=end_dt, key="ga4aud_end")

    # ---------- Comparación ----------
    compare_label = st.radio(
        "Comparar contra",
        ["Sin comparación", "Período anterior", "Mismo período del año anterior"],
        index=1, horizontal=True, key="ga4aud_compare"
    )
    compare_map = {
        "Sin comparación": "none",
        "Período anterior": "previous_period",
        "Mismo período del año anterior": "yoy",
    }
    compare_mode = compare_map[compare_label]

    # ---------- Series temporales ----------
    gran = st.radio("Granularidad de la serie", ["Diaria","Semanal","Mensual"], index=0, horizontal=True, key="ga4aud_gran")
    gran_map = {"Diaria":"date","Semanal":"week","Mensual":"month"}
    granularity = gran_map[gran]

    # ---------- Métricas ----------
    st.markdown("**Métricas**")
    mc1, mc2, mc3, mc4 = st.columns(4)
    with mc1:
        m_users = st.checkbox("Usuarios (totalUsers)", True, key="ga4aud_m_users")
        m_new   = st.checkbox("Usuarios nuevos (newUsers)", True, key="ga4aud_m_new")
    with mc2:
        m_sessions  = st.checkbox("Sesiones (sessions)", True, key="ga4aud_m_sessions")
        m_eng_sess  = st.checkbox("Sesiones con engagement (engagedSessions)", True, key="ga4aud_m_eng")
    with mc3:
        m_eng_rate  = st.checkbox("Engagement rate (engagementRate)", True, key="ga4aud_m_rate")
        m_avg_eng   = st.checkbox("Tiempo medio de engagement (averageEngagementTime)", True, key="ga4aud_m_avg")
    with mc4:
        m_events    = st.checkbox("Eventos (eventCount)", False, key="ga4aud_m_events")
        m_views     = st.checkbox("Vistas (screenPageViews)", False, key="ga4aud_m_views")
        inc_video = st.checkbox("Incluir métricas de video (starts/completes)", value=False, key="ga4_inc_video")

# --- Eventos personalizados (sumar eventCount por nombre) ---
    with st.expander("Eventos personalizados (sumar eventCount por nombre de evento)"):
        raw_events = st.text_input(
            "Nombres de eventos separados por coma",
            value=st.session_state.get("ga4aud_custom_events_raw",""),
            key="ga4aud_custom_events_raw",
            help="Coinciden EXACTAMENTE con 'eventName' en GA4 (respetar mayúsculas/minúsculas). Ej: Start, adStarted, adComplete, Pause"
        )
        custom_events = [e.strip() for e in raw_events.split(",") if e.strip()]
        if custom_events:
            st.caption("Se crearán columnas por evento: ev_<nombre>. Ej: ev_Start, ev_adStarted, ev_adComplete…")
        else:
            st.caption("Dejalo vacío si no querés sumar eventos personalizados.")


    # ---------- Desgloses (tablas TOP) ----------
    st.markdown("**Desgloses (se crean tablas TOP por cada uno que elijas)**")
    d1, d2, d3, d4 = st.columns(4)
    with d1:
        b_ch   = st.checkbox("Canal (defaultChannelGroup)", True, key="ga4aud_b_ch")
        b_srcm = st.checkbox("Source / Medium", True, key="ga4aud_b_srcm")
    with d2:
        b_dev  = st.checkbox("Dispositivo (deviceCategory)", True, key="ga4aud_b_dev")
        b_ctry = st.checkbox("País (country)", True, key="ga4aud_b_ctry")
    with d3:
        b_city = st.checkbox("Ciudad (city)", False, key="ga4aud_b_city")
        b_lang = st.checkbox("Idioma (language)", False, key="ga4aud_b_lang")
    with d4:
        b_age  = st.checkbox("Edad (age)", False, key="ga4aud_b_age")
        b_gen  = st.checkbox("Género (gender)", False, key="ga4aud_b_gen")

    top_n = st.number_input("Filas por tabla TOP", min_value=5, max_value=1000, value=25, step=5, key="ga4aud_topn")

    # ---------- Filtros opcionales ----------
    with st.expander("Filtros opcionales"):
        f_dev  = st.selectbox("Filtrar por dispositivo", ["(Todos)","desktop","mobile","tablet"], index=0, key="ga4aud_f_dev")
        f_ch   = st.selectbox("Filtrar por canal", ["(Todos)","Organic Search","Direct","Paid Search","Display","Referral","Email","Social","(Other)"], index=0, key="ga4aud_f_ch")
        f_ctry = st.text_input("Filtrar por países (ISO-2 o nombre, separados por coma)", value="", key="ga4aud_f_ctry")

    # ---------- Ensamble de parámetros ----------
    metrics = {
        "totalUsers": m_users,
        "newUsers": m_new,
        "sessions": m_sessions,
        "engagedSessions": m_eng_sess,
        "engagementRate": m_eng_rate,
        "averageEngagementTime": m_avg_eng,
        "eventCount": m_events,
        "screenPageViews": m_views,
    }
    breakdowns = []
    if b_ch:   breakdowns.append("defaultChannelGroup")
    if b_srcm: breakdowns.append("sourceMedium")
    if b_dev:  breakdowns.append("deviceCategory")
    if b_ctry: breakdowns.append("country")
    if b_city: breakdowns.append("city")
    if b_lang: breakdowns.append("language")
    if b_age:  breakdowns.append("age")
    if b_gen:  breakdowns.append("gender")

    filters = {
        "deviceCategory": None if f_dev == "(Todos)" else f_dev,
        "defaultChannelGroup": None if f_ch == "(Todos)" else f_ch,
        "countries": [c.strip() for c in f_ctry.split(",") if c.strip()] if f_ctry.strip() else [],
    }

    params = {
    "start": start_date,
    "end": end_date,
    "compare": compare_mode,
    "granularity": granularity,
    "metrics": metrics,
    "breakdowns": breakdowns,
    "include_video_metrics": bool(inc_video),
    "custom_event_names": custom_events,
    "top_n": int(top_n),
    "filters": filters,
    "sheet_title_prefix": "GA4 Audiencia",
    "lag_days": int(lag),
    }

    pid = st.session_state.get("ga4_property_id")
    pname = st.session_state.get("ga4_property_name", f"Propiedad {pid}") if pid else "—"

    can_run = start_date <= end_date
    if st.button("👥 Generar Reporte de audiencia (GA4)", type="primary", disabled=not can_run, key="ga4aud_run"):
        try:
            sid = run_with_indicator(
                "Generando Reporte de audiencia (GA4)",
                run_ga4_audience_report,   # runner externo/local
                ga4_data,                  # cliente GA4 Data API
                drive_service, 
                gs_client,  # servicios Google
                st.session_state["ga4_property_id"],
                params,                    # parámetros
                st.session_state.get("dest_folder_id")
            )
            if sid:
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
                    analysis_kind="GA4 Audiencia",
                    sheet_id=sid, sheet_name=sheet_name, sheet_url=sheet_url,
                    gsc_account=(st.session_state.get("src_account_label") or "") + f" / {pname}",
                    notes=f"win={start_date}->{end_date}, compare={compare_mode}, gran={granularity}, top_n={int(top_n)}"
                )
                st.session_state["last_file_id"] = sid
                st.session_state["last_file_kind"] = "ga4_audience"
                st.session_state["post_actions_visible"] = True
        except Exception as e:
            st.error(f"Falló la generación del reporte de audiencia: {e}")

elif analisis == "12":
    # ===== NUEVO: Incorp. y permanencia en Discover (10 días horarios) =====
    _require_sc_or_stop()
    if run_discover_retention is None:
        st.warning("Este despliegue no incluye `run_discover_retention` (agregalo en el repo externo).")
        st.stop()

    st.subheader("Incorporación y permanencia en Google Discover (10 días horarios)")
    st.caption("Analiza datos horarios (UTC) de los últimos 10 días, con detección de fecha de publicación por cascada o XPath, y filtros por sección/país/dispositivo.")

    # --- Ventana: últimos 10 días con lag
    lag = st.number_input("Lag de días (evitar datos incompletos)", 0, 5, 3, 1, key="disc_ret_lag")
    anchor = date.today() - timedelta(days=int(lag))
    start_date = anchor - timedelta(days=9)
    end_date = anchor
    st.info(f"Período analizado (UTC): **{start_date} → {end_date}**")

    # --- Detección de fecha de publicación
    mode = st.radio("Detección de fecha de publicación", ["Automática (cascada)", "XPath personalizado"],
                    index=0, horizontal=True, key="disc_ret_mode")
    xp_pub = ""
    if mode.startswith("XPath"):
        xp_pub = st.text_input(
            "XPath de fecha/hora de publicación (UTC)",
           value="", key="disc_ret_xpath",
            help="Ej.: //meta[@property='article:published_time']/@content  o  //time/@datetime"
        )

   # --- Filtros
    with st.expander("Filtros", expanded=False):
        section = st.text_input("Sección (path comienza con, ej: /deportes/)", value="", key="disc_ret_section")
        device = st.selectbox("Dispositivo", ["(Todos)","DESKTOP","MOBILE","TABLET"], index=0, key="disc_ret_device")
        country = st.text_input("País (ISO-3, ej: ARG/ESP/USA)", value="", key="disc_ret_country").strip().upper()

    # --- Ejecutar
    if st.button("📊 Ejecutar análisis de Discover (incorporación/permanencia)", type="primary", key="disc_ret_run"):
        params = {
            "window": {
                "mode": "last_10_days",
               "lag_days": int(lag),
                "start": start_date,
                "end": end_date,
                "timezone": "UTC",
               "hourly": True,
            },
            "publication_time": {
                "mode": "auto" if mode.startswith("Automática") else "xpath",
                "xpath": xp_pub or "",
                "cascade": True,
               "timezone": "UTC",
            },
            "filters": {
                "section": section or None,
               "device": None if device == "(Todos)" else device,
                "country": country or None,  # ISO-3
            },
           "template_sheet_id": "1SB9wFHWyDfd5P-24VBP7-dE1f1t7YvVYjnsc2XjqU8M",
            "sheet_title_prefix": "Incorp. y permanencia Discover",
        }
        if len(site_urls) <= 1:
            sid = run_with_indicator(
                "Procesando Discover (incorp./permanencia)",
                run_discover_retention,
                sc_service, drive_service, gs_client, site_url, params,
                st.session_state.get("dest_folder_id")
            )
            if sid:
                maybe_prefix_sheet_name_with_medio(drive_service, sid, site_url)
                st.success("¡Listo! Tu documento está creado.")
                st.markdown(f"➡️ **Abrir Google Sheets**: https://docs.google.com/spreadsheets/d/{sid}")
                with st.expander("Compartir acceso al documento (opcional)"):
                    share_controls(drive_service, sid, default_email=_me.get("emailAddress") if _me else None)
                st.session_state.update(last_file_id=sid, last_file_kind="discover_retention", post_actions_visible=True)
        else:
            results = run_for_sites(
                "Procesando Discover (incorp./permanencia)",
                run_discover_retention,
                sc_service, drive_service, gs_client, site_urls, params, st.session_state.get("dest_folder_id")
            )
            st.success(f"¡Listo! Se generaron {len(results)} documentos.")
            for s, sid in results:
                st.markdown(f"• **{s}** → https://docs.google.com/spreadsheets/d/{sid}")
            if results:
                st.session_state.update(last_file_id=results[-1][1], last_file_kind="discover_retention", post_actions_visible=True)

elif analisis == "9":
    # ===== Análisis de contenido (runner externo, con shim de normalización) =====
    if (run_content_analysis is None) or (params_for_content is None):
        st.warning("Este despliegue no incluye `run_content_analysis` y/o `params_for_content` (repo externo). "
                   "Actualizá el paquete `seo_analisis_ext` para habilitarlo.")
    else:
        _require_sc_or_stop()  # requiere SC
        st.subheader("Análisis de contenido (externo)")
        st.caption("Analiza rendimiento por contenido con filtros por país/dispositivo/sección y periodo; genera Sheets con tablas y series. (Usa runner externo).")

        # Parámetros UI (reutiliza tu módulo de params)
        p = params_for_content()

        # Botón de ejecución (soporta multi-sitio)
        if st.button("🧩 Ejecutar Análisis de contenido (externo)", type="primary", key="btn_content_ext"):
            try:
                if len(site_urls) <= 1:
                    sid = run_with_indicator(
                        "Procesando Análisis de contenido (externo)",
                        run_content_analysis,  # shim envuelve y normaliza params
                        sc_service, drive_service, gs_client, site_url, p,
                        st.session_state.get("dest_folder_id")
                    )
                    if sid:
                        maybe_prefix_sheet_name_with_medio(drive_service, sid, site_url)
                        st.success("¡Listo! Tu documento está creado.")
                        st.markdown(f"➡️ **Abrir Google Sheets**: https://docs.google.com/spreadsheets/d/{sid}")
                        with st.expander("Compartir acceso al documento (opcional)"):
                            share_controls(drive_service, sid, default_email=_me.get("emailAddress") if _me else None)
                        activity_log_append(
                            drive_service, gs_client,
                            user_email=( _me or {}).get("emailAddress") or "",
                            event="analysis", site_url=site_url,
                            analysis_kind="Análisis de contenido",
                            sheet_id=sid, sheet_name="", sheet_url=f"https://docs.google.com/spreadsheets/d/{sid}",
                            gsc_account=st.session_state.get("src_account_label") or "",
                            notes=f"params={p!r}"
                        )
                        st.session_state["last_file_id"] = sid
                        st.session_state["last_file_kind"] = "content_analysis"
                        st.session_state["post_actions_visible"] = True
                else:
                    results = run_for_sites(
                        "Procesando Análisis de contenido (externo)",
                        run_content_analysis, sc_service, drive_service, gs_client, site_urls, p,
                        st.session_state.get("dest_folder_id")
                    )
                    st.success(f"¡Listo! Se generaron {len(results)} documentos.")
                    for s, sid in results:
                        st.markdown(f"• **{s}** → https://docs.google.com/spreadsheets/d/{sid}")
                        activity_log_append(
                            drive_service, gs_client,
                            user_email=( _me or {}).get("emailAddress") or "",
                            event="analysis", site_url=s,
                            analysis_kind="Análisis de contenido",
                            sheet_id=sid, sheet_name="", sheet_url=f"https://docs.google.com/spreadsheets/d/{sid}",
                            gsc_account=st.session_state.get("src_account_label") or "",
                            notes=f"params={p!r}"
                        )
                    if results:
                        st.session_state["last_file_id"] = results[-1][1]
                        st.session_state["last_file_kind"] = "content_analysis"
                        st.session_state["post_actions_visible"] = True
            except Exception as e:
                st.error(f"Falló el análisis de contenido: {e}")

elif analisis == "10":
    _require_sc_or_stop()
    # ===== ANÁLISIS DE ESTRUCTURA DE CONTENIDOS =====
    st.subheader("Análisis de estructura de contenidos")
    st.caption("Trae URLs por Search / Discover, filtra y scrapea **solo los campos que elijas** (en el scope del artículo si indicas su XPath) y publica en Sheets. Incluye extracción de entidades con spaCy.")
    if len(site_urls) > 1:
        st.info("🔁 Seleccionaste múltiples sitios. El *preview* y conteos se muestran para el **primer** sitio seleccionado; al ejecutar, se procesarán **todos** y se creará un Sheet por cada uno.")

    # ========== Config básica: fechas + origen + límite ==========
    colA, colB, colC = st.columns([1,1,2])
    with colA:
        start_date = st.date_input("Desde (inclusive)", value=(date.today() - timedelta(days=28)), key="fast_start")
    with colB:
        end_default = date.today() - timedelta(days=2)
        end_date = st.date_input("Hasta (inclusive)", value=end_default, key="fast_end")
    with colC:
        tipo = st.radio("Origen", ["Search", "Discover", "Search + Discover"], horizontal=True, key="fast_source")

    # Validación de rango de fechas
    if start_date > end_date:
        st.error("La fecha 'Desde' no puede ser posterior a 'Hasta'. Corrigelo para continuar.")
        st.stop()

    col0a, col0b = st.columns([1,1])
    with col0a:
        row_limit = st.number_input("Máximo de URLs por origen", min_value=10, max_value=5000, value=500, step=10, key="fast_row_lim")
    with col0b:
        st.write("")  # espaciador

    # ========== Opciones avanzadas ==========
    with st.expander("⚙️ Opciones avanzadas de configuración y filtrado", expanded=False):
        col1, col2, col3 = st.columns(3)
        with col1:
            country = st.text_input("País (ISO-3166-1 alpha-3, ej: ARG, USA, ESP)", value=st.session_state.get("fast_country","")).strip().upper()
            st.session_state["fast_country"] = country
        with col2:
            device = st.selectbox("Dispositivo", ["(Todos)","DESKTOP","MOBILE","TABLET"], index=0, key="fast_device")
        with col3:
            order_by = st.selectbox("Ordenar por", ["clicks","impressions","ctr","position"], index=0, key="fast_order")

        col4, col5, col6 = st.columns(3)
        with col4:
            min_clicks = st.number_input("Mínimo de clics", min_value=0, max_value=1000000, value=0, step=10, key="fast_min_clicks")
        with col5:
            min_impr = st.number_input("Mínimo de impresiones", min_value=0, max_value=10000000, value=0, step=100, key="fast_min_impr")
        with col6:
            only_articles = st.checkbox("Solo artículos (filtra tags/player/etc.)", value=True, key="fast_only_articles")

        st.markdown("---")
        col7, col8, col9 = st.columns(3)
        with col7:
            concurrency = st.slider("Concurrencia", 2, 64, 24, step=2, key="fast_conc")
        with col8:
            timeout_s = st.slider("Timeout por página (s)", 5, 30, 12, step=1, key="fast_timeout")
        with col9:
            ua = st.text_input("User-Agent (opcional)", value=st.session_state.get("fast_ua",""), key="fast_ua")
            if not ua.strip():
                st.caption("Sugerencia UA (si ves muchos 403):")
                st.code(_suggest_user_agent(""))

        joiner = st.text_input("Separador para listas (H2/H3/Tags/Anchors/Negritas)", value=st.session_state.get("joiner"," | "), key="joiner")

    # ========== Campos a extraer ==========
    st.markdown("### 🧩 Campos a extraer")

    # Default state para widgets (para evitar warnings por set_state + default)
    FIELD_KEYS = [
        "w_title","w_h1","w_md","w_ogt","w_ogd","w_canon","w_pub","w_lang",
        "w_firstp","w_article_text",
        "w_h2_list","w_h2_count","w_h3_list","w_h3_count",
        "w_bold","w_bold_list","w_links","w_link_anchors",
        "w_tags","w_rel_count","w_rel_anchors",
        "fast_entities"
    ]
    DEFAULTS = {
        "w_title": True, "w_h1": True, "w_md": True, "w_ogt": False, "w_ogd": False,
        "w_canon": True, "w_pub": False, "w_lang": False,
        "w_firstp": True, "w_article_text": False,
        "w_h2_list": False, "w_h2_count": False, "w_h3_list": False, "w_h3_count": False,
        "w_bold": False, "w_bold_list": False, "w_links": False, "w_link_anchors": False,
        "w_tags": False, "w_rel_count": False, "w_rel_anchors": False,
        "fast_entities": True
    }
    for k, v in DEFAULTS.items():
        if k not in st.session_state:
            st.session_state[k] = v

    # Botones seleccionar/deseleccionar
    col_sel_a, col_sel_b = st.columns([1,1])
    with col_sel_a:
        if st.button("Seleccionar todo", key="btn_sel_all"):
            for k in FIELD_KEYS:
                st.session_state[k] = True
            st.rerun()
    with col_sel_b:
        if st.button("Deseleccionar todo", key="btn_unsel_all"):
            for k in FIELD_KEYS:
                st.session_state[k] = False
            st.rerun()

    colX, colY = st.columns(2)
    with colX:
        w_title = st.checkbox("Title", value=st.session_state["w_title"], key="w_title")
        w_h1 = st.checkbox("H1", value=st.session_state["w_h1"], key="w_h1")
        w_md = st.checkbox("Meta description", value=st.session_state["w_md"], key="w_md")
        w_ogt = st.checkbox("OG:title", value=st.session_state["w_ogt"], key="w_ogt")
        w_ogd = st.checkbox("OG:description", value=st.session_state["w_ogd"], key="w_ogd")
        w_canon = st.checkbox("Canonical", value=st.session_state["w_canon"], key="w_canon")
        w_pub = st.checkbox("Fecha publicación (meta/time)", value=st.session_state["w_pub"], key="w_pub")
        w_lang = st.checkbox("Lang (html@lang)", value=st.session_state["w_lang"], key="w_lang")
        w_firstp = st.checkbox("Primer párrafo (XPath opcional)", value=st.session_state["w_firstp"], key="w_firstp")
        xp_firstp = st.text_input("XPath Primer párrafo (opcional)", value=st.session_state.get("xp_firstp",""), key="xp_firstp",
                                  help="Ej: //article//p[normalize-space()][1]  |  relativo si empieza con .//")

        # XPath del contenedor del artículo
        xp_article = st.text_input("XPath del contenedor del artículo (recomendado)",
                                   value=st.session_state.get("xp_article",""),
                                   key="xp_article",
                                   help="Define el scope de h2/h3/negritas/links. Ej: //article | //main[@id='content'] | .//div[@data-type='article-body']")

        # Caja de noticias relacionadas
        st.markdown("**Caja de noticias relacionadas**")
        w_rel_count = st.checkbox("Cantidad de links en caja de relacionadas", value=st.session_state["w_rel_count"], key="w_rel_count")
        w_rel_anchors = st.checkbox("Anchor text de relacionadas (lista)", value=st.session_state["w_rel_anchors"], key="w_rel_anchors")
        xp_related = st.text_input("XPath de la caja de relacionadas (contenedor)",
                                   value=st.session_state.get("xp_related",""),
                                   key="xp_related",
                                   help="Ej: //aside[contains(@class,'related')] | //section[@id='relacionadas']")

        # Texto completo del artículo (para ponderar entidades)
        w_article_text = st.checkbox("Texto del artículo (para ponderar entidades)", value=st.session_state["w_article_text"], key="w_article_text")
        xp_article_text_hint = st.caption("Si el XPath de contenedor está bien definido, no hace falta XPath adicional para el texto.")

    with colY:
        w_h2_list = st.checkbox("H2 (lista, SOLO dentro del artículo)", value=st.session_state["w_h2_list"], key="w_h2_list")
        w_h2_count = st.checkbox("H2 (cantidad, SOLO dentro del artículo)", value=st.session_state["w_h2_count"], key="w_h2_count")
        xp_h2 = st.text_input("XPath H2 (opcional)", value=st.session_state.get("xp_h2",""), key="xp_h2",
                              help="Si empieza con .// se aplica respecto del contenedor; si no, se usa .//h2 por defecto.")
        w_h3_list = st.checkbox("H3 (lista, SOLO dentro del artículo)", value=st.session_state["w_h3_list"], key="w_h3_list")
        w_h3_count = st.checkbox("H3 (cantidad, SOLO dentro del artículo)", value=st.session_state["w_h3_count"], key="w_h3_count")
        xp_h3 = st.text_input("XPath H3 (opcional)", value=st.session_state.get("xp_h3",""), key="xp_h3",
                              help="Si empieza con .// se aplica respecto del contenedor; si no, se usa .//h3 por defecto.")
        w_bold = st.checkbox("Cantidad de negritas (SOLO dentro del artículo)", value=st.session_state["w_bold"], key="w_bold")
        w_bold_list = st.checkbox("Lista de negritas (SOLO dentro del artículo)", value=st.session_state["w_bold_list"], key="w_bold_list")
        w_links = st.checkbox("Cantidad de links (SOLO dentro del artículo)", value=st.session_state["w_links"], key="w_links")
        w_link_anchors = st.checkbox("Anchor text de links del artículo (lista)", value=st.session_state["w_link_anchors"], key="w_link_anchors")
        w_tags = st.checkbox("Tags (lista)", value=st.session_state["w_tags"], key="w_tags")
        xp_tags = st.text_input("XPath Tags (opcional)", value=st.session_state.get("xp_tags",""), key="xp_tags",
                                help="Ej: .//ul[@class='tags']//a | //meta[@property='article:tag']/@content")

    # ========== Preflight/preview con el PRIMER sitio ==========
    preview_site = site_url
    seeds = []
    seeds_search = []
    seeds_discover = []
    src_map = {"Search":"web","Discover":"discover","Search + Discover":"both"}
    src = src_map.get(tipo, "both")

    # Traer semillas (solo preview)
    if src in ("web","both"):
        seeds_search = _gsc_fetch_top_urls(
            sc_service, preview_site, start_date, end_date, "web",
            country or (None if country == "(TODOS)" else None),
            device if device and device != "(Todos)" else None,
            order_by, int(row_limit)
        )
    if src in ("discover","both"):
        seeds_discover = _gsc_fetch_top_urls(
            sc_service, preview_site, start_date, end_date, "discover",
            country or (None if country == "(TODOS)" else None),
            device if device and device != "(Todos)" else None,
            order_by, int(row_limit)
        )

    if seeds_search:
        for r in seeds_search:
            r["source"] = "Search"
        seeds.extend(seeds_search)
    if seeds_discover:
        for r in seeds_discover:
            r["source"] = "Discover"
        seeds.extend(seeds_discover)

    if st.session_state.get("DEBUG"):
        st.markdown("### 🔎 Semillas desde GSC (modo debug)")
        if "_fast_error" in st.session_state:
            st.error(st.session_state["_fast_error"])
        st.write(f"**Search (web)**: {len(seeds_search):,} filas | **Discover**: {len(seeds_discover):,} filas")
        st.code([r.get("page","") for r in (seeds_search[:10] if seeds_search else [])])
        st.code([r.get("page","") for r in (seeds_discover[:10] if seeds_discover else [])])

    df_seeds_preview = pd.DataFrame(seeds)
    if not df_seeds_preview.empty:
        before = len(df_seeds_preview)
        if min_clicks > 0:
            df_seeds_preview = df_seeds_preview[df_seeds_preview["clicks"] >= int(min_clicks)]
        if min_impr > 0:
            df_seeds_preview = df_seeds_preview[df_seeds_preview["impressions"] >= int(min_impr)]
        if st.session_state.get("DEBUG"):
            st.caption(f"Tras umbrales (preview {preview_site}): {len(df_seeds_preview):,} (antes {before:,})")
        df_seeds_preview["ctr_pct"] = (df_seeds_preview["ctr"].fillna(0) * 100).round(2)
        df_seeds_preview = df_seeds_preview.rename(columns={"page":"url"})
        df_seeds_preview = df_seeds_preview.sort_values(["url","clicks"], ascending=[True,False]).drop_duplicates(subset=["url"], keep="first")
        urls_preview = df_seeds_preview["url"].dropna().astype(str).tolist()
        if only_articles:
            urls_preview = _filter_article_urls(urls_preview)
        st.write(f"URLs candidatas a scraping (preview: **{preview_site}**): **{len(urls_preview):,}**")
    else:
        st.info("No se obtuvieron semillas para el preview. Aun así podés ejecutar (se procesarán todos los sitios seleccionados).")

    # ========== Ejecutar ==========
    def _run_structure_for_site(one_site: str):
        # Traer semillas reales para este sitio
        seeds_s, seeds_d = [], []
        if src in ("web","both"):
            seeds_s = _gsc_fetch_top_urls(
                sc_service, one_site, start_date, end_date, "web",
                country or (None if country == "(TODOS)" else None),
                device if device and device != "(Todos)" else None,
                order_by, int(row_limit)
            )
        if src in ("discover","both"):
            seeds_d = _gsc_fetch_top_urls(
                sc_service, one_site, start_date, end_date, "discover",
                country or (None if country == "(TODOS)" else None),
                device if device and device != "(Todos)" else None,
                order_by, int(row_limit)
            )
        lst = []
        if seeds_s:
            for r in seeds_s:
                r["source"] = "Search"
            lst.extend(seeds_s)
        if seeds_d:
            for r in seeds_d:
                r["source"] = "Discover"
            lst.extend(seeds_d)

        df_seeds = pd.DataFrame(lst)
        if df_seeds.empty:
            st.warning(f"({one_site}) No hay semillas en el rango/filtros. Se omite.")
            return None

        before = len(df_seeds)
        if min_clicks > 0:
            df_seeds = df_seeds[df_seeds["clicks"] >= int(min_clicks)]
        if min_impr > 0:
            df_seeds = df_seeds[df_seeds["impressions"] >= int(min_impr)]

        df_seeds["ctr_pct"] = (df_seeds["ctr"].fillna(0) * 100).round(2)
        df_seeds = df_seeds.rename(columns={"url":"url"})
        df_seeds = df_seeds.sort_values(["url","clicks"], ascending=[True,False]).drop_duplicates(subset=["url"], keep="first")

        urls = df_seeds["url"].dropna().astype(str).tolist()
        if only_articles:
            urls = _filter_article_urls(urls)
        if not urls:
            st.warning(f"({one_site}) No hay URLs para scrapear tras filtros. Se omite.")
            return None

        ua_final = _suggest_user_agent(st.session_state.get("fast_ua",""))

        # Armar wants/xpaths según checkboxes
        wants = {
            "title": st.session_state["w_title"], "h1": st.session_state["w_h1"], "meta_description": st.session_state["w_md"],
            "og_title": st.session_state["w_ogt"], "og_description": st.session_state["w_ogd"], "canonical": st.session_state["w_canon"],
            "published_time": st.session_state["w_pub"], "lang": st.session_state["w_lang"],
            "first_paragraph": st.session_state["w_firstp"], "article_text": st.session_state["w_article_text"],
            "h2_list": st.session_state["w_h2_list"], "h2_count": st.session_state["w_h2_count"],
            "h3_list": st.session_state["w_h3_list"], "h3_count": st.session_state["w_h3_count"],
            "bold_count": st.session_state["w_bold"], "bold_list": st.session_state["w_bold_list"],
            "link_count": st.session_state["w_links"], "link_anchor_texts": st.session_state["w_link_anchors"],
            "related_links_count": st.session_state["w_rel_count"], "related_link_anchors": st.session_state["w_rel_anchors"],
            "tags_list": st.session_state["w_tags"]
        }
        xpaths = {
            "article": st.session_state.get("xp_article",""),
            "first_paragraph": st.session_state.get("xp_firstp",""),
            "h2": st.session_state.get("xp_h2",""),
            "h3": st.session_state.get("xp_h3",""),
            "tags": st.session_state.get("xp_tags",""),
            "related_box": st.session_state.get("xp_related",""),
        }

        if not any(wants.values()):
            st.error("Seleccioná al menos un campo para extraer."); return None

        # Scraping (async si hay aiohttp)
        try:
            results = asyncio.run(_scrape_async(
                urls, ua_final, wants=wants, xpaths=xpaths, joiner=st.session_state.get("joiner"," | "),
                timeout_s=int(timeout_s), concurrency=int(concurrency)))
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            results = loop.run_until_complete(_scrape_async(
                urls, ua_final, wants=wants, xpaths=xpaths, joiner=st.session_state.get("joiner"," | "),
                timeout_s=int(timeout_s), concurrency=int(concurrency)))
            loop.close()

        df_scr = pd.DataFrame(results)

        # ===== ENTIDADES con spaCy =====
        entities_enabled = st.session_state.get("fast_entities", True)
        if entities_enabled:
            # Asegurar columnas base
            for col in ["h1","meta_description","first_paragraph","article_text"]:
                if col not in df_scr.columns:
                    df_scr[col] = ""

            ents_top = []
            ents_all = []
            try:
                nlp, model_id, how = ensure_spacy()
                from collections import Counter
                for _, r in df_scr.iterrows():
                    h1v = str(r.get("h1","")) or ""
                    mdv = str(r.get("meta_description","")) or ""
                    fpv = str(r.get("first_paragraph","")) or ""
                    art = str(r.get("article_text","")) or ""

                    text_combo = " ".join([h1v, mdv, fpv]).strip()
                    if not text_combo:
                        ents_top.append("")
                        ents_all.append("")
                        continue

                    doc = nlp(text_combo)
                    items = [e.text.strip() for e in doc.ents if e.text and e.text.strip()]
                    if not items:
                        ents_top.append("")
                        ents_all.append("")
                        continue

                    c = Counter()
                    art_lower = art.lower()
                    for it in items:
                        w = 1
                        if it and art_lower.count(it.lower()):
                            w += 2
                        if h1v.lower().count(it.lower()):
                            w += 2
                        if fpv.lower().count(it.lower()):
                            w += 1
                        c[it] += w

                    top10 = [t for t, _ in c.most_common(10)]
                    ents_top.append(st.session_state.get("joiner"," | ").join(top10))
                    ents_all.append(st.session_state.get("joiner"," | ").join(sorted(c.keys(), key=lambda x: (-c[x], x.lower()))))

            except Exception as e:
                st.warning(f"No pude preparar spaCy: {e}")
                ents_top = ["" for _ in range(len(df_scr))]
                ents_all = ["" for _ in range(len(df_scr))]

            df_scr["entities_top"] = ents_top
            df_scr["entities_all"] = ents_all
        else:
            df_scr["entities_top"] = ""
            df_scr["entities_all"] = ""

        # Merge con métricas de GSC
        df_seeds = df_seeds.rename(columns={"url":"url"})
        df_out = pd.merge(
            df_seeds[["url","source","clicks","impressions","ctr_pct","position"]],
            df_scr, on="url", how="left"
        )

        # Columnas dinámicas según wants
        cols = ["source","url"]
        if st.session_state["w_h1"]: cols.append("h1")
        if st.session_state["w_title"]: cols.append("title")
        if st.session_state["w_md"]: cols.append("meta_description")
        if st.session_state["w_ogt"]: cols.append("og_title")
        if st.session_state["w_ogd"]: cols.append("og_description")
        if st.session_state["w_canon"]: cols.append("canonical")
        if st.session_state["w_pub"]: cols.append("published_time")
        if st.session_state["w_lang"]: cols.append("lang")
        if st.session_state["w_firstp"]: cols.append("first_paragraph")
        if st.session_state["w_article_text"]: cols.append("article_text")
        if st.session_state["w_h2_list"]: cols.append("h2_list")
        if st.session_state["w_h2_count"]: cols.append("h2_count")
        if st.session_state["w_h3_list"]: cols.append("h3_list")
        if st.session_state["w_h3_count"]: cols.append("h3_count")
        if st.session_state["w_bold"]: cols.append("bold_count")
        if st.session_state["w_bold_list"]: cols.append("bold_list")
        if st.session_state["w_links"]: cols.append("link_count")
        if st.session_state["w_link_anchors"]: cols.append("link_anchor_texts")
        if st.session_state["w_rel_count"]: cols.append("related_links_count")
        if st.session_state["w_rel_anchors"]: cols.append("related_link_anchors")
        if st.session_state["w_tags"]: cols.append("tags_list")
        # entidades
        cols += ["entities_top","entities_all"]
        # métricas
        cols += ["clicks","impressions","ctr_pct","position","status","error"]

        # Asegurar columnas
        for c in cols:
            if c not in df_out.columns:
                df_out[c] = "" if c not in ("clicks","impressions","ctr_pct","position","status") else 0
        df_out = df_out[cols]

        # Renombrado final de columnas (pedido)
        rename_map = {
            "source": "Search / Discover",
            "url": "URL",
            "h1": "H1",
            "title": "Title",
            "meta_description": "Meta Description",
            "og_title": "OG Title",
            "og_description": "OG Description",
            "canonical": "Canonical",
            "first_paragraph": "Primer Párrafo",
            "article_text": "Texto del Artículo",
            "h2_list": "H2 (lista)",
            "h2_count": "H2 (cantidad)",
            "h3_list": "H3 (lista)",
            "h3_count": "H3 (cantidad)",
            "bold_count": "Negritas (cantidad)",
            "bold_list": "Negritas (lista)",
            "link_count": "Links (cantidad)",
            "link_anchor_texts": "Links (anchors)",
            "related_links_count": "Relacionadas (links)",
            "related_link_anchors": "Relacionadas (anchors)",
            "tags_list": "Tags",
            "entities_top": "Entidades (Top 10)",
            "entities_all": "Entidades (Todas)",
            "clicks": "Clics",
            "impressions": "Impresiones",
            "ctr_pct": "CTR",
            "position": "Posición",
            "status": "Status",
            "error": "Error"
        }
        df_out = df_out.rename(columns=rename_map)

        # Crear Sheet en Drive
        name = f"Estructura ({start_date} a {end_date}) - {one_site.replace('https://','').replace('http://','').strip('/')}"
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

        maybe_prefix_sheet_name_with_medio(drive_service, sid, one_site)

        activity_log_append(
            drive_service, gs_client,
            user_email=( _me or {}).get("emailAddress") or "",
            event="analysis", site_url=one_site,
            analysis_kind="Estructura de contenidos",
            sheet_id=sid, sheet_name=name, sheet_url=f"https://docs.google.com/spreadsheets/d/{sid}",
            gsc_account=st.session_state.get("src_account_label") or "",
            notes=f"win={start_date}->{end_date}, src={tipo}, urls={len(urls)}"
        )
        return sid, df_out

    can_run = True  # se valida arriba rango de fechas
    if st.button("⚡ Ejecutar scraping + exportar a Sheets", type="primary", disabled=not can_run, key="fast_run"):
        results_cs: list[tuple[str,str]] = []
        if len(site_urls) <= 1:
            out = _run_structure_for_site(site_url)
            if out:
                sid, df_out = out
                st.success("¡Listo! Tu documento está creado.")
                st.markdown(f"➡️ **Abrir Google Sheets**: https://docs.google.com/spreadsheets/d/{sid}")
                with st.expander("Compartir acceso al documento (opcional)"):
                    share_controls(drive_service, sid, default_email=_me.get("emailAddress") if _me else None)
                st.session_state["last_file_id"] = sid
                st.session_state["last_file_kind"] = "content_structure"
                st.session_state["post_actions_visible"] = True
                with st.expander("Vista previa (primeras 20 filas)"):
                    st.dataframe(df_out.head(20), use_container_width=True)
        else:
            prog = st.progress(0.0)
            for i, s in enumerate(site_urls, 1):
                out = _run_structure_for_site(s)
                if out:
                    sid, _df = out
                    results_cs.append((s, sid))
                prog.progress(i/len(site_urls))
            prog.empty()
            if results_cs:
                st.success(f"¡Listo! Se generaron {len(results_cs)} documentos.")
                for s, sid in results_cs:
                    st.markdown(f"• **{s}** → https://docs.google.com/spreadsheets/d/{sid}")
                st.session_state["last_file_id"] = results_cs[-1][1]
                st.session_state["last_file_kind"] = "content_structure"
                st.session_state["post_actions_visible"] = True

else:
    st.info("La opción 1 aún no esta disponible en esta versión.")

def show_post_run_actions(gs_client, sheet_id: str, kind: str, site_url: str | None = None):
    import streamlit as st
    from google.oauth2.credentials import Credentials

    st.divider()
    st.subheader("Acciones posteriores")
    st.caption("Elegí qué querés hacer ahora:")

    # Sufijo ESTABLE (sin uuid) para que los widgets no se 'destilden' al recargar
    def _stable_suffix():
        base = (site_url or "global").replace("https://","").replace("http://","").replace("/","_")
        return f"{kind}_{sheet_id}_{base}"

    suffix = _stable_suffix()

    # Checkboxes con claves estables y valores persistentes
    sum_key = f"post_sum_{suffix}"
    doc_key = f"post_doc_{suffix}"
    slack_key = f"post_slack_{suffix}"

    if sum_key not in st.session_state:
        st.session_state[sum_key] = True
    if doc_key not in st.session_state:
        st.session_state[doc_key] = False
    if slack_key not in st.session_state:
        st.session_state[slack_key] = False

    do_sum = st.checkbox("🤖 Resumen del análisis generado con Nomadic BOT",
                         value=st.session_state[sum_key], key=sum_key)
    do_doc = st.checkbox("🤖 Documento de texto basado en el análisis de Nomadic BOT",
                         value=st.session_state[doc_key], key=doc_key)
    do_slack = st.checkbox("Resumen del análisis para enviar a Slack (A desarrollar)",
                           value=st.session_state[slack_key], key=slack_key)

    if st.button("Ejecutar acciones seleccionadas", type="primary", key=f"post_go_{suffix}"):
        selected = [do_sum, do_doc, do_slack]
        if sum(1 for x in selected if x) == 0:
            st.info("Seleccioná al menos una acción.")
            return

        progress = st.progress(0.0)
        done = 0

        # Reusar resumen previo si ya existe
        summary_text = (
            st.session_state.get("last_summary_text")
            or st.session_state.get("gemini_last_text")
            or ""
        )

        # Si solo tildó Doc y no hay resumen, igual generamos resumen primero.
        need_summary = do_sum or (do_doc and not summary_text)

        # 1) Resumen IA
        if need_summary:
            try:
                with st.spinner("Generando resumen con Nomadic BOT…"):
                    from modules.app_ai import gemini_summary
                    txt = gemini_summary(gs_client, sheet_id, kind=kind, widget_suffix=f"post_{suffix}") or ""
                if txt.strip():
                    summary_text = txt.strip()
                    st.session_state["last_summary_text"] = summary_text
                    st.success("Resumen IA generado ✅")
                else:
                    st.warning("No se obtuvo texto de resumen (vacío).")
            except Exception as e:
                st.error(f"Falló el resumen IA: {e}")
            done += 1
            progress.progress(done / 3.0)

        # 2) Documento de texto (respeta estilo del template: reemplaza {{CONTENT}})
        doc_url = None
        if do_doc:
            if not summary_text:
                st.warning("⚠️ No hay un resumen disponible. Primero generá el **Resumen IA** para poder crear el Doc.")
            else:
                creds_dest_dict = st.session_state.get("creds_dest") or {}
                scopes_have = set(creds_dest_dict.get("scopes") or [])
                if not has_docs_scope(scopes_have):
                    st.error("Tu sesión NO tiene permisos de Google Docs. Repetí el Paso 0 habilitando el scope de Docs.")
                else:
                    try:
                        creds_personal = Credentials(**creds_dest_dict)
                        sheet_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}"

                        # ---- Título: "Medio - Nombre de análisis - AAAA-MM-DD"
                        def _pretty_medium_name(u: str | None) -> str:
                            if not u:
                                return "Documento"
                            v = str(u).replace("https://", "").replace("http://", "")
                            if v.startswith("www."):
                                v = v[4:]
                            v = v.split("/")[0].strip()
                            return v or "Documento"

                        def _analysis_pretty(k: str | None) -> str:
                            mapping = {
                                "core": "Core Update",
                                "audit": "Auditoría de tráfico",
                                "evergreen": "Evergreen",
                                "sections": "Análisis de secciones",
                                "report_results": "Reporte de resultados",
                                "ga4_audience": "GA4 Audiencia",
                                "content_structure": "Estructura de contenidos",
                                "discover": "Discover Snoop",
                                "names": "Análisis de Nombres",
                            }
                            kk = (k or "").strip().lower()
                            return mapping.get(kk, f"Análisis {k or ''}".strip() or "Análisis")

                        medio_name = _pretty_medium_name(site_url)
                        analysis_name = _analysis_pretty(kind)
                        title_guess = f"{medio_name} - {analysis_name} - {date.today():%Y-%m-%d}"

                        # El creador reemplaza el marcador {{CONTENT}} y preserva el estilo del párrafo del template.
                        content = (summary_text if summary_text else "(Resumen no disponible)") \
                                  + f"\n\n—\n➡️ Sheet del análisis: {sheet_url}"

                        with st.spinner("Creando Google Doc…"):
                            doc_id = create_doc_from_template_with_content(
                                credentials=creds_personal,
                                title=title_guess,
                                analysis_text=content,
                                dest_folder_id=st.session_state.get("dest_folder_id")
                            )
                            doc_url = f"https://docs.google.com/document/d/{doc_id}"
                            st.success("Documento de texto creado ✅")
                    except Exception as e:
                        st.error(f"Falló la creación del Doc: {e}")
            done += 1
            progress.progress(done / 3.0)

        # 3) Mensaje para Slack (placeholder)
        if do_slack:
            sheet_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}"
            head = f"*{analysis_name if 'analysis_name' in locals() else (kind or 'Análisis')}*"
            if site_url:
                head += f" — `{site_url}`"
            body = (summary_text or "Resumen pendiente de generar.").strip()
            msg = f"{head}\n{sheet_url}\n\n{body}"
            st.text_area(
                "Mensaje para Slack (copiá y pegá en tu canal)",
                value=msg,
                height=220,
                key=f"slack_msg_{suffix}"
            )
            st.success("Mensaje listo ✅")
            done += 1
            progress.progress(done / 3.0)

        progress.empty()
        st.markdown("### Enlaces")
        st.markdown(f"• **Google Sheets** → https://docs.google.com/spreadsheets/d/{sheet_id}")
        if doc_url:
            st.markdown(f"• **Google Doc** → {doc_url}")


# --- Acciones posteriores (mostrar solo tras ejecutar un análisis) ---
if (
    st.session_state.get("post_actions_visible") and
    st.session_state.get("last_file_id") and
    st.session_state.get("last_file_kind")
):
    show_post_run_actions(
        gs_client=gs_client,
        sheet_id=st.session_state["last_file_id"],
        kind=st.session_state["last_file_kind"],
        site_url=st.session_state.get("site_url_choice")
    )

# --- Debug info ---
if st.session_state.get("DEBUG"):
    st.write("¿Gemini listo?", "GEMINI_API_KEY" in st.secrets)
