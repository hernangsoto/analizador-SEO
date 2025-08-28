# app.py
from __future__ import annotations

# --- Permisos OAuth en localhost + tolerancia de scope
import os
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

import json, sys
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

# ---- Shims (compat) ----
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

# ====== Módulos locales ======
from modules.app_config import apply_base_style_and_logo, get_app_home
from modules.app_ext import USING_EXT, run_core_update, run_evergreen, run_traffic_audit, run_names_analysis
from modules.app_utils import get_qp, clear_qp, has_gsc_scope, norm
from modules.app_ai import load_prompts, gemini_healthcheck, gemini_summary, ai_source
from modules.app_params import params_for_core_update, params_for_evergreen, params_for_auditoria, params_for_names
from modules.app_activity import maybe_prefix_sheet_name_with_medio, activity_log_append
from modules.app_errors import run_with_indicator
from modules.app_auth_flow import step0_google_identity, logout_screen

# ====== Google modules ======
from modules.drive import ensure_drive_clients, get_google_identity, pick_destination, share_controls
from modules.gsc import ensure_sc_client

# ====== Estilo / branding ======
apply_base_style_and_logo()
st.title("Analizador SEO 🚀")

# ---------- IA / Prompts ----------
load_prompts()
try:
    ok, _ = gemini_healthcheck()
    if not ok:
        st.caption("💡 Podés cargar una API key de Gemini en Secrets (GEMINI_API_KEY o [gemini].api_key).")
except Exception:
    pass

# ============== App ==============

APP_HOME = get_app_home()

# Detectar pantalla de logout
_view = get_qp().get("view")
if isinstance(_view, list):
    _view = _view[0] if _view else None
if _view == "logout":
    logout_screen(APP_HOME); st.stop()

# Preferir Paso 0 (OIDC + Drive + GSC en un paso)
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

# Usuario "sintético" para la UI si no viene de Streamlit Sharing
if not user:
    if ident:
        user = SimpleNamespace(
            is_logged_in=True,
            name=(ident.get("name") or "Invitado"),
            email=(ident.get("email") or "—"),
            picture=(ident.get("picture")),
        )
    else:
        login_screen(); st.stop()

# Sidebar → Mantenimiento
def maintenance_extra_ui():
    if USING_EXT:
        st.caption("🧩 Usando análisis del paquete externo (repo privado).")
    else:
        st.caption("🧩 Usando análisis embebidos en este repo.")
    st.checkbox("🔧 Modo debug (Drive/GSC)", key="DEBUG")
sidebar_user_info(user, maintenance_extra=maintenance_extra_ui)

# ====== Clientes Google a partir del Paso 0 ======
if not st.session_state.get("creds_dest"):
    st.error("No se detectaron credenciales personales. Reintentá el inicio de sesión.")
    st.stop()

try:
    creds_personal = Credentials(**st.session_state["creds_dest"])
    drive_service, gs_client = ensure_drive_clients(creds_personal)
    _me = get_google_identity(drive_service)
    st.session_state["_google_identity"] = _me or st.session_state.get("_google_identity", {})
    email_txt = (_me or {}).get("emailAddress") or "email desconocido"
    st.markdown(
        f'''
        <div class="success-inline">
            Guardaremos los informes en el Drive de: <strong>{email_txt}</strong>
            <a href="{APP_HOME}?view=logout" target="_self" rel="nofollow">(Cerrar sesión)</a>
        </div>
        ''',
        unsafe_allow_html=True
    )
    # Log del login
    activity_log_append(
        drive_service, gs_client,
        user_email=email_txt, event="login",
        gsc_account=st.session_state.get("src_account_label") or "",
        notes="Paso 0 completo (OIDC + Drive/Sheets + GSC)"
    )
except Exception as e:
    st.error(f"No pude inicializar Drive/Sheets con la cuenta personal: {e}")
    st.stop()

# --- Paso carpeta destino (opcional) ---
st.session_state.setdefault("step2_done", False)
if not st.session_state["step2_done"]:
    with st.expander("Destino de la copia (opcional)", expanded=False):
        st.caption("Por defecto el archivo se guardará en **Mi unidad (raíz)**. Si querés otra carpeta, elegila acá.")
        dest_folder_id = pick_destination(drive_service, _me, show_header=False)
        if st.button("Guardar selección de carpeta", key="btn_save_step2"):
            st.session_state["step2_done"] = True
            st.rerun()
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

# ---------- Paso 1: elegir análisis ----------
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
    # Panel de resumen (solo uno, no duplicado)
    if st.session_state.get("last_file_id") and st.session_state.get("last_file_kind"):
        st.divider(); st.subheader("📄 Resumen del análisis")
        st.caption("Podés generar o regenerar el resumen sin volver a ejecutar el análisis.")
        gemini_summary(gs_client, st.session_state["last_file_id"],
                       kind=st.session_state["last_file_kind"], widget_suffix="panel")
    st.stop()

# ======== Paso 2: elegir CUENTA para Search Console ========
st.subheader("¿Con qué cuenta querés consultar Search Console?")
account_options = ["Acceso en cuenta personal de Nomadic", "Acceso", "Acceso Medios"]
_default_label = st.session_state.get("sc_account_choice", "Acceso en cuenta personal de Nomadic")
default_idx = account_options.index(_default_label) if _default_label in account_options else 0
sc_choice = st.selectbox("Cuenta de acceso a SC", account_options, index=default_idx, key="sc_account_choice")

from modules.auth import build_flow
GSC_SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]

def _choice_to_key(label: str) -> str | None:
    if norm(label) == norm("Acceso"): return "ACCESO"
    if norm(label) == norm("Acceso Medios"): return "ACCESO_MEDIOS"
    return None

# a) Personal: reusar token del paso 0 (ya incluye GSC)
sc_service = None
if sc_choice == "Acceso en cuenta personal de Nomadic":
    if not has_gsc_scope(st.session_state["creds_dest"].get("scopes")):
        st.error("El inicio de sesión personal no incluye permiso de Search Console. Cerrá sesión e iniciá nuevamente.")
        st.stop()
    try:
        creds_src = Credentials(**st.session_state["creds_dest"])
        sc_service = ensure_sc_client(creds_src)
        st.session_state["creds_src"] = st.session_state["creds_dest"]
        st.session_state["src_account_label"] = sc_choice
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

# b) Acceso / Acceso Medios: abrir DIRECTO el flujo de esa cuenta (copy/paste localhost)
else:
    wanted_key = _choice_to_key(sc_choice)
    # Creamos un flujo fijando esa cuenta, sin radios extra
    key = f"oauth_src_forced_{wanted_key}"
    if key not in st.session_state:
        flow = build_flow(wanted_key, GSC_SCOPES)
        auth_url, state = flow.authorization_url(
            prompt="consent select_account",
            access_type="offline",
        )
        st.session_state[key] = {"flow": flow, "auth_url": auth_url, "state": state, "account": sc_choice}

    osrc = st.session_state[key]
    st.markdown(f"🔗 **Autorizar {sc_choice}:** [Abrir autorización de Google]({osrc['auth_url']})")
    url = st.text_input(
        "Pegá la URL completa de redirección (http://localhost/?code=...&state=...)",
        key=f"auth_response_url_src_{wanted_key}",
        placeholder="http://localhost/?code=...&state=...",
    )

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Conectar Search Console", key=f"btn_connect_src_{wanted_key}", type="secondary"):
            if not url.strip():
                st.error("Pegá la URL completa que te abrió Google (incluye code y state)."); st.stop()
            from urllib.parse import urlsplit, parse_qs
            try:
                qs = parse_qs(urlsplit(url.strip()).query)
                returned_state = (qs.get("state") or [""])[0]
            except Exception:
                returned_state = ""
            if returned_state != osrc["state"]:
                st.error("CSRF Warning: el 'state' devuelto no coincide. Tocá 'Reiniciar' y probá de nuevo."); st.stop()
            try:
                osrc["flow"].fetch_token(authorization_response=url.strip())
                creds_src = osrc["flow"].credentials
                st.session_state["creds_src"] = {
                    "token": creds_src.token,
                    "refresh_token": getattr(creds_src, "refresh_token", None),
                    "token_uri": creds_src.token_uri,
                    "client_id": creds_src.client_id,
                    "client_secret": creds_src.client_secret,
                    "scopes": creds_src.scopes,
                }
                st.session_state["src_account_label"] = sc_choice
                st.session_state["step3_done"] = True
                st.experimental_rerun()
            except Exception as e:
                st.error("No se pudo conectar Search Console. Volvé a intentar."); 
                if st.session_state.get("DEBUG"): st.caption(f"Detalle: {e}")

    with c2:
        if st.button("Reiniciar autorización", key=f"btn_reset_src_{wanted_key}"):
            st.session_state.pop(key, None)
            for k in ("creds_src","oauth_src","step3_done","src_account_label"):
                st.session_state.pop(k, None)
            clear_qp(); st.rerun()

# --- Paso 3: Elegir sitio (GSC) ---
def pick_site(sc_service):
    st.subheader("Elegí el sitio a analizar")
    try:
        site_list = sc_service.sites().list().execute()
        sites = site_list.get("siteEntry", [])
    except Exception as e:
        st.error(f"Error al obtener sitios: {e}"); st.stop()
    verified = [s for s in sites if s.get("permissionLevel") != "siteUnverifiedUser"]
    if not verified:
        st.error("No se encontraron sitios verificados en esta cuenta."); st.stop()
    options = sorted({s["siteUrl"] for s in verified})
    prev = st.session_state.get("site_url_choice")
    index = options.index(prev) if prev in options else 0
    return st.selectbox("Sitio verificado:", options, index=index, key="site_url_choice")

site_url = pick_site(sc_service)

# --- Paso 4: Ejecutar análisis ---
if analisis == "4":
    if run_core_update is None:
        st.warning("Este despliegue no incluye run_core_update.")
    else:
        params = params_for_core_update()
        if st.button("🚀 Ejecutar análisis de Core Update", type="primary"):
            sid = run_with_indicator(
                "Procesando Core Update",
                run_core_update, sc_service, drive_service, gs_client, site_url, params,
                st.session_state.get("dest_folder_id"),
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
                user_email=(_me or {}).get("emailAddress") or "",
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
                st.session_state.get("dest_folder_id"),
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
                user_email=(_me or {}).get("emailAddress") or "",
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
                st.session_state.get("dest_folder_id"),
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
                user_email=(_me or {}).get("emailAddress") or "",
                event="analysis", site_url=site_url,
                analysis_kind="Auditoría",
                sheet_id=sid, sheet_name=sheet_name, sheet_url=sheet_url,
                gsc_account=st.session_state.get("src_account_label") or "",
                notes=f"params={params!r}"
            )
            st.session_state["last_file_id"] = sid
            st.session_state["last_file_kind"] = "audit"
else:
    st.info("Las opciones 1, 2 y 3 aún no están disponibles en esta versión.")

# --- Panel de resumen (una sola vez, sin duplicar) ---
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