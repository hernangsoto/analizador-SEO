from __future__ import annotations

# --- Permisos OAuth en localhost + tolerancia de scope
import os
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

import json
import time
import sys
from types import SimpleNamespace
from datetime import date, timedelta, datetime

import pandas as pd
import streamlit as st
from google.oauth2.credentials import Credentials

# ====== Config base (debe ser el 1er st.*)
try:
    st.set_page_config(layout="wide", page_title="Análisis SEO", page_icon="📊")
except Exception:
    pass

# ------------------------------------------------------------------
# Shims de compatibilidad
# ------------------------------------------------------------------
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
from modules.ui import (
    apply_page_style,
    get_user,
    sidebar_user_info,
    login_screen,
)

# ====== Carga de módulos locales ======
from modules.app_config import apply_base_style_and_logo, get_app_home
from modules.app_ext import USING_EXT, run_core_update, run_evergreen, run_traffic_audit, run_names_analysis
from modules.app_utils import get_qp, clear_qp, oauth_flow_store, has_gsc_scope, norm
from modules.app_ai import (
    load_prompts, ai_source, ai_import_error, prompts_map, gemini_healthcheck,
    render_prompt_probe, gemini_summary,
)
from modules.app_params import (
    params_for_core_update, params_for_evergreen, params_for_auditoria, params_for_names,
)
from modules.app_activity import maybe_prefix_sheet_name_with_medio, activity_log_append
from modules.app_errors import show_google_error, run_with_indicator
from modules.app_auth_flow import step0_google_identity, logout_screen
from modules.app_diagnostics import scan_repo_for_gsc_and_filters, read_context

# ====== Google modules ======
from modules.auth import pick_destination_oauth, pick_source_oauth, build_flow
from modules.drive import ensure_drive_clients, get_google_identity, pick_destination, share_controls
from modules.gsc import ensure_sc_client

# ====== Estilo / branding ======
apply_base_style_and_logo()
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

# Preferir Paso 0 (OIDC)
prefer_oidc = bool(st.secrets.get("auth", {}).get("prefer_oidc", True))

ident = st.session_state.get("_google_identity")
user = get_user()

if prefer_oidc and st.session_state.get("_auth_bypass"):
    st.session_state.pop("_auth_bypass", None)
    user = None

if prefer_oidc and not ident:
    ident = step0_google_identity()
    if not ident:
        st.stop()

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
st.session_state.setdefault("step3_done", False)

# === Acciones de query ===
_qp = get_qp()
_action = _qp.get("action")
if isinstance(_action, list):
    _action = _action[0] if _action else None

if _action == "change_personal":
    for k in ("creds_dest", "oauth_dest", "step1_done"):
        st.session_state.pop(k, None)
    st.session_state["step2_done"] = False
    st.session_state.pop("dest_folder_id", None)
    clear_qp(); st.rerun()
elif _action == "change_folder":
    st.session_state["step2_done"] = False
    clear_qp(); st.rerun()
elif _action == "change_src":
    for k in ("creds_src", "oauth_src", "step3_done", "src_account_label"):
        st.session_state.pop(k, None)
    st.session_state.pop("sc_account_choice", None)
    st.session_state.pop("_src_oauth_in_progress", None)
    st.session_state.pop("_src_target_label", None)
    clear_qp(); st.rerun()

# --- PASO 1: OAuth PERSONAL (Drive/Sheets) ---
creds_dest = None
if not st.session_state["step1_done"]:
    id_email = (st.session_state.get("_google_identity") or {}).get("email")
    if id_email:
        st.markdown(
            f'''
            <div class="success-inline">
                Sesión iniciada como <strong>{id_email}</strong>. Usá esta misma cuenta al autorizar Drive/Sheets.
            </div>
            ''',
            unsafe_allow_html=True
        )
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

# Si ya está completo, clientes + resumen + log login
drive_service = None
gs_client = None
_me = None

if st.session_state["step1_done"] and st.session_state.get("creds_dest"):
    try:
        creds_dest = Credentials(**st.session_state["creds_dest"])
        drive_service, gs_client = ensure_drive_clients(creds_dest)
        _me = get_google_identity(drive_service)
        st.session_state["_google_identity"] = _me or st.session_state.get("_google_identity", {})
        email_txt = (_me or {}).get("emailAddress") or "email desconocido"
        st.markdown(
            f'''
            <div class="success-inline">
                Los archivos se guardarán en el Drive de: <strong>{email_txt}</strong>
                <a href="{APP_HOME}?action=change_personal" target="_self" rel="nofollow">(Cambiar mail personal)</a>
            </div>
            ''',
            unsafe_allow_html=True
        )
        activity_log_append(
            drive_service, gs_client,
            user_email=email_txt, event="login",
            gsc_account=st.session_state.get("src_account_label") or "",
            notes="OIDC + Drive/Sheets listos"
        )
    except Exception as e:
        st.error(f"No pude inicializar Drive/Sheets con la cuenta PERSONAL: {e}")
        st.stop()

# --- PASO 2: Carpeta destino (opcional) ---
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
def pick_analysis(include_auditoria: bool, include_names: bool = True):
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

    key = st.radio("Tipos disponibles:", opciones, index=3, key="analysis_choice")
    if key.startswith("4."): return "4"
    if key.startswith("5."): return "5"
    if key.startswith("6."): return "6"
    if key.startswith("7."): return "7"
    return "0"

analisis = pick_analysis(include_auditoria, include_names=True)

# ---------- Rama especial: Nombres ----------
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
                gemini_summary(gs_client, sid, kind="names", widget_suffix="after_run")
    if st.session_state.get("last_file_id") and st.session_state.get("last_file_kind"):
        st.divider(); st.subheader("📄 Resumen del análisis")
        st.caption("Podés generar o regenerar el resumen sin volver a ejecutar el análisis.")
        gemini_summary(gs_client, st.session_state["last_file_id"],
                       kind=st.session_state["last_file_kind"], widget_suffix="panel")
    st.stop()

# ======== Resto de análisis (requieren GSC) ========

def _csrf_mismatch_hint(step_label: str = "Paso 2"):
    st.error("CSRF Warning: el 'state' devuelto no coincide con el generado.")
    st.info(f"Hacé clic en **Reiniciar {step_label}** y repetí la autorización (un solo click).")
    if st.button(f"Reiniciar {step_label}", key=f"btn_restart_{step_label.replace(' ', '_').lower()}"):
        for k in ("creds_src","oauth_src","step3_done","src_account_label","_src_oauth_in_progress","_src_target_label"):
            st.session_state.pop(k, None)
        clear_qp(); st.rerun()

# --- NUEVO: flujo forzado para SC (sin radio duplicado) ---
GSC_SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]

def pick_source_oauth_forced(account_key: str) -> Credentials | None:
    """
    Igual a pick_source_oauth pero bloqueando la cuenta (sin radio).
    """
    st.subheader("2) Conectar cuenta de Search Console (fuente de datos)")
    # Rehidratación por account_key
    key = f"oauth_src_{account_key}"
    if key not in st.session_state:
        flow = build_flow(account_key, GSC_SCOPES)
        auth_url, state = flow.authorization_url(
            prompt="consent select_account",
            access_type="offline",
            include_granted_scopes=True,
        )
        st.session_state[key] = {
            "account": account_key,
            "flow": flow,
            "auth_url": auth_url,
            "state": state,
        }

    osrc = st.session_state[key]
    st.markdown(f"🔗 **Paso A (fuente):** [Autorizar acceso a Search Console]({osrc['auth_url']})")
    with st.expander("Ver/copiar URL de autorización (fuente)"):
        st.code(osrc["auth_url"])

    url = st.text_input(
        "🔑 Paso B (fuente): pegá la URL completa (http://localhost/?code=...&state=...)",
        key=f"auth_response_url_src_{account_key}",
        placeholder="http://localhost/?code=...&state=...",
    )

    c1, c2 = st.columns([1,1])
    creds = None
    with c1:
        if st.button("Conectar Search Console", key=f"btn_connect_src_{account_key}", type="secondary"):
            if not url.strip():
                st.error("Pegá la URL completa de redirección (incluye code y state).")
                st.stop()
            from urllib.parse import urlsplit, parse_qs
            try:
                qs = parse_qs(urlsplit(url.strip()).query)
                returned_state = (qs.get("state") or [""])[0]
            except Exception:
                returned_state = ""
            expected_state = osrc.get("state")
            if not returned_state or returned_state != expected_state:
                _csrf_mismatch_hint("Paso 2")
                st.stop()
            try:
                flow = osrc["flow"]
                flow.fetch_token(authorization_response=url.strip())
                creds = flow.credentials
            except Exception as e:
                st.error("No se pudo conectar Search Console. Reintentá autorización y pegá la URL completa.")
                st.caption(f"Detalle técnico: {e}")

    with c2:
        if st.button("Reiniciar Paso 2", key=f"btn_reset_src_{account_key}"):
            st.session_state.pop(key, None)
            for k in ("creds_src","oauth_src","step3_done","src_account_label",
                      "_src_oauth_in_progress","_src_target_label"):
                st.session_state.pop(k, None)
            clear_qp(); st.rerun()

    return creds

# --- Selección de cuenta SC (sin duplicar la pregunta luego) ---
st.subheader("Selecciona la cuenta con acceso a Search Console")
account_options = ["Acceso", "Acceso Medios", "Acceso en cuenta personal de Nomadic"]
_default_label = st.session_state.get("sc_account_choice", "Acceso en cuenta personal de Nomadic")
default_idx = account_options.index(_default_label) if _default_label in account_options else 2

sc_choice = st.selectbox(
    "Elegí la cuenta para consultar datos de Search Console",
    account_options, index=default_idx, key="sc_account_choice"
)

# Mapeo a clave de secrets
def _choice_to_key(label: str) -> str | None:
    if norm(label) == norm("Acceso"): return "ACCESO"
    if norm(label) == norm("Acceso Medios"): return "ACCESO_MEDIOS"
    return None  # personal usa creds_dest

if sc_choice == "Acceso en cuenta personal de Nomadic":
    creds_dest_dict = st.session_state.get("creds_dest")
    if not creds_dest_dict:
        st.error("No encuentro la sesión principal. Volvé a iniciar sesión en el Paso 0."); st.stop()
    if not has_gsc_scope(creds_dest_dict.get("scopes")):
        st.warning("Tu cuenta personal no tiene permisos de Search Console todavía.")
        c1, c2 = st.columns([1,3])
        with c1:
            if st.button("➕ Añadir permiso de Search Console", key="btn_add_gsc_scope"):
                for k in ("oauth_oidc","_google_identity","creds_dest","step1_done"):
                    st.session_state.pop(k, None)
                clear_qp(); st.rerun()
        with c2:
            st.caption("Se reabrirá el Paso 0 pidiendo también el permiso de Search Console.")
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
    # >>> Aquí va el cambio: mostramos DIRECTO el login de la cuenta elegida, sin radio duplicado
    wanted_key = _choice_to_key(sc_choice)  # "ACCESO" o "ACCESO_MEDIOS"
    need_new_auth = (
        not st.session_state.get("step3_done") or
        norm(st.session_state.get("src_account_label")) != norm(sc_choice) or
        norm(st.session_state.get("src_account_label")) == norm("Acceso en cuenta personal de Nomadic")
    )

    if need_new_auth:
        # Ejecutar directamente el flujo para la cuenta elegida
        creds_src_obj = pick_source_oauth_forced(wanted_key)
        if not creds_src_obj:
            st.stop()

        st.session_state["creds_src"] = {
            "token": creds_src_obj.token,
            "refresh_token": getattr(creds_src_obj, "refresh_token", None),
            "token_uri": creds_src_obj.token_uri,
            "client_id": creds_src_obj.client_id,
            "client_secret": creds_src_obj.client_secret,
            "scopes": creds_src_obj.scopes,
        }
        st.session_state["src_account_label"] = sc_choice
        st.session_state["step3_done"] = True
        clear_qp(); st.rerun()
    else:
        try:
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

# --- PASO 4: sitio ---
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
            gemini_summary(gs_client, sid, kind="core", force_prompt_key="core", widget_suffix="after_run")

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
            gemini_summary(gs_client, sid, kind="evergreen", widget_suffix="after_run")

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
            gemini_summary(gs_client, sid, kind="audit", widget_suffix="after_run")

else:
    st.info("Las opciones 1, 2 y 3 aún no están disponibles en esta versión.")

# --- Panel persistente de resumen (aparece una sola vez) ---
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