# app.py
from __future__ import annotations

# --- Permisos OAuth en localhost + tolerancia de scope
import os
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

import json
from datetime import datetime
from types import SimpleNamespace

import pandas as pd
import streamlit as st
from google.oauth2.credentials import Credentials
from gspread.exceptions import APIError as GspreadAPIError

# ====== Configuración/branding ======
from modules.app_config import setup_page_and_branding
from modules.app_constants import (
    APP_HOME, SCOPES_GSC, LAG_DAYS_DEFAULT,
)
from modules.ui import get_user, sidebar_user_info, login_screen

setup_page_and_branding()
st.title("Analizador SEO 🚀")

# ====== Capa externa / runners ======
from modules.app_ext import load_runners
run_core_update, run_evergreen, run_traffic_audit, run_names_analysis, USING_EXT = load_runners()

# ====== Auth y clientes Google ======
from modules.auth import (
    build_flow,
    pick_destination_oauth,
    pick_source_oauth,
    SCOPES_DRIVE,
)
from modules.drive import (
    ensure_drive_clients,
    get_google_identity,
    pick_destination,
    share_controls,
)
from modules.gsc import ensure_sc_client

# ====== Utilidades de la app ======
from modules.app_utils import (
    get_qp, clear_qp,
    oauth_flow_store,
    run_with_indicator,
    maintenance_extra_ui,
    pick_site, pick_analysis,
    has_gsc_scope, norm,
    revoke_google_token,
)
from modules.app_errors import show_google_error
from modules.app_activity import (
    maybe_prefix_sheet_name_with_medio,
    activity_log_append,
)
from modules.app_params import (
    params_for_core_update,
    params_for_evergreen,
    params_for_auditoria,
    params_for_names,
)
from modules.app_ai import (
    gemini_summary,
    render_prompt_probe,
    gemini_healthcheck,
    AI_SRC,
)
from modules.app_diagnostics import render_gsc_filters_debug_panel
from modules.utils import token_store

# ============== Navegación (logout por query) ==============
_qp = get_qp()
_view = _qp.get("view")
if isinstance(_view, list):
    _view = _view[0] if _view else None

if _view == "logout":
    st.header("Cerrar sesión")
    ident = st.session_state.get("_google_identity") or {}
    current_email = ident.get("email") or "—"
    st.write(f"Usuario actual: **{current_email}**")

    revoke = st.checkbox("Revocar permisos de Google (Drive/Sheets y Search Console)", value=True)
    wipe_pkg = st.checkbox("Borrar caché del paquete externo (.ext_pkgs/)", value=False)

    c1, c2 = st.columns(2)
    with c1:
        if st.button("🔒 Cerrar sesión y limpiar", type="primary"):
            if revoke:
                for key in ("creds_dest", "creds_src"):
                    data = st.session_state.get(key)
                    if isinstance(data, dict):
                        revoke_google_token(data.get("token") or data.get("refresh_token"))
            try: st.cache_data.clear()
            except Exception: pass
            try: st.cache_resource.clear()
            except Exception: pass
            if wipe_pkg:
                import shutil
                shutil.rmtree(".ext_pkgs", ignore_errors=True)
            for k in [
                "_auth_bypass", "_google_identity",
                "oauth_oidc", "oauth_dest", "oauth_src",
                "creds_dest", "creds_src",
                "step1_done", "step2_done", "step3_done",
                "dest_folder_id", "src_account_label",
                "site_url_choice", "last_file_id", "last_file_kind",
                "sc_account_choice",
                "DEBUG",
            ]:
                st.session_state.pop(k, None)
            try:
                token_store.clear("creds_dest")
                token_store.clear("creds_src")
            except Exception:
                pass
            st.markdown(f"<meta http-equiv='refresh' content='0; url={APP_HOME}'>", unsafe_allow_html=True)
            st.stop()
    with c2:
        if st.button("Cancelar"):
            clear_qp()
            st.rerun()

# ============== Paso 0: identidad OIDC + Drive/Sheets + GSC ==============
prefer_oidc = bool(st.secrets.get("auth", {}).get("prefer_oidc", True))

ident = st.session_state.get("_google_identity")
user = get_user()

if prefer_oidc and st.session_state.get("_auth_bypass"):
    st.session_state.pop("_auth_bypass", None)
    user = None

from modules.app_auth_flow import step0_google_identity
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

# ============== Sidebar / mantenimiento ==============
sidebar_user_info(user, maintenance_extra=lambda: maintenance_extra_ui(USING_EXT))

# Estados de pasos
st.session_state.setdefault("step1_done", False)
st.session_state.setdefault("step2_done", False)
st.session_state.setdefault("step3_done", False)

# === Acciones de query ===
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
        st.caption("Por defecto el archivo se guardará en **Mi unidad (raíz)**. Si querés otra carpeta, abrí este panel y elegila aquí.")
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

# --- Elegir análisis (antes de GSC para habilitar la rama de Nombres sin GSC) ---
include_auditoria = run_traffic_audit is not None
analisis = pick_analysis(include_auditoria, include_names=True)

# ========== Rama especial: Análisis de Nombres (no requiere GSC) ==========
if analisis == "7":
    if run_names_analysis is None:
        st.warning("Este despliegue no incluye `run_names_analysis` (analysis_names.py). Subilo al repo privado o a modules/ y recargá.")
    else:
        params_names = params_for_names()
        total = len(params_names.get("items") or [])
        if total == 0:
            st.info("Cargá un CSV o pegá al menos un nombre para habilitar la ejecución.")
        else:
            if st.button("🔎 Ejecutar Análisis de Nombres (KG + Wikipedia)", type="primary"):
                sid = run_with_indicator(
                    "Procesando Análisis de Nombres (KG + Wikipedia)",
                    run_names_analysis,
                    drive_service, gs_client,
                    params_names,
                    st.session_state.get("dest_folder_id")
                )
                st.success("¡Listo! Tu documento está creado.")
                st.markdown(f"➡️ **Abrir Google Sheets**: https://docs.google.com/spreadsheets/d/{sid}")

                with st.expander("Compartir acceso al documento (opcional)"):
                    share_controls(drive_service, sid, default_email=_me.get("emailAddress") if _me else None)

                try:
                    meta = drive_service.files().get(fileId=sid, fields="name,webViewLink").execute()
                    sheet_name = meta.get("name", "")
                    sheet_url = meta.get("webViewLink") or f"https://docs.google.com/spreadsheets/d/{sid}"
                except Exception:
                    sheet_name = ""
                    sheet_url = f"https://docs.google.com/spreadsheets/d/{sid}"

                activity_log_append(
                    drive_service, gs_client,
                    user_email=(_me or {}).get("emailAddress") or "",
                    event="analysis",
                    site_url="",
                    analysis_kind="Nombres (KG+Wikipedia)",
                    sheet_id=sid, sheet_name=sheet_name, sheet_url=sheet_url,
                    gsc_account="",
                    notes=f"lang={params_names.get('lang')}, n={total}"
                )

                st.session_state["last_file_id"] = sid
                st.session_state["last_file_kind"] = "names"
                gemini_summary(gs_client, sid, kind="names", widget_suffix="after_run")

    if st.session_state.get("last_file_id") and st.session_state.get("last_file_kind"):
        st.divider()
        st.subheader("📄 Resumen del análisis")
        st.caption("Podés generar o regenerar el resumen sin volver a ejecutar el análisis.")
        gemini_summary(
            gs_client,
            st.session_state["last_file_id"],
            kind=st.session_state["last_file_kind"],
            widget_suffix="panel"
        )
    st.stop()

# ======== Resto de análisis (sí requieren GSC) ========
st.subheader("Selecciona la cuenta con acceso a Search Console")
account_options = ["Acceso", "Acceso Medios", "Acceso en cuenta personal de Nomadic"]
_default_label = st.session_state.get("sc_account_choice", "Acceso en cuenta personal de Nomadic")
default_idx = account_options.index(_default_label) if _default_label in account_options else 2

sc_choice = st.selectbox(
    "Elegí la cuenta para consultar datos de Search Console",
    account_options, index=default_idx, key="sc_account_choice"
)

sc_service = None

if sc_choice == "Acceso en cuenta personal de Nomadic":
    creds_dest_dict = st.session_state.get("creds_dest")
    if not creds_dest_dict:
        st.error("No encuentro la sesión principal. Volvé a iniciar sesión en el Paso 0.")
        st.stop()

    if not has_gsc_scope(creds_dest_dict.get("scopes")):
        st.warning("Tu cuenta personal no tiene permisos de Search Console todavía.")
        c1, c2 = st.columns([1,3])
        with c1:
            if st.button("➕ Añadir permiso de Search Console", key="btn_add_gsc_scope"):
                for k in ("oauth_oidc", "_google_identity", "creds_dest", "step1_done"):
                    st.session_state.pop(k, None)
                st.experimental_set_query_params()
                st.rerun()
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
    wanted_norm = norm(sc_choice)
    have_label = st.session_state.get("src_account_label")
    have_norm = norm(have_label)
    need_new_auth = (
        not st.session_state.get("step3_done")
        or (have_norm != wanted_norm)
        or (have_norm == norm("Acceso en cuenta personal de Nomadic"))
    )

    if need_new_auth:
        for k in ("creds_src", "oauth_src", "step3_done", "src_account_label"):
            st.session_state.pop(k, None)

        st.info(f"Conectá la cuenta **{sc_choice}** para Search Console.")
        creds_src_obj = pick_source_oauth()
        if not creds_src_obj:
            st.stop()

        picked_label = (st.session_state.get("oauth_src") or {}).get("account") or ""
        picked_norm = norm(picked_label)

        if picked_norm != wanted_norm:
            st.error(f"Autorizaste **{picked_label}**, pero seleccionaste **{sc_choice}**. Reintentá el login eligiendo la cuenta correcta.")
            if st.button("Reintentar selección de cuenta", key="retry_wrong_sc_account"):
                for k in ("creds_src", "oauth_src", "step3_done", "src_account_label"):
                    st.session_state.pop(k, None)
                st.rerun()
            st.stop()

        st.session_state["creds_src"] = {
            "token": creds_src_obj.token,
            "refresh_token": getattr(creds_src_obj, "refresh_token", None),
            "token_uri": creds_src_obj.token_uri,
            "client_id": creds_src_obj.client_id,
            "client_secret": creds_src_obj.client_secret,
            "scopes": creds_src_obj.scopes,
        }
        st.session_state["src_account_label"] = picked_label
        st.session_state["step3_done"] = True
        st.rerun()
    else:
        try:
            creds_src = Credentials(**st.session_state["creds_src"])
            sc_service = ensure_sc_client(creds_src)
            src_label = st.session_state.get("src_account_label") or sc_choice
            st.markdown(
                f'''
                <div class="success-inline">
                    Cuenta de acceso (Search Console): <strong>{src_label}</strong>
                    <a href="?action=change_src" target="_self" rel="nofollow">(Cambiar cuenta de acceso)</a>
                </div>
                ''',
                unsafe_allow_html=True
            )
        except Exception as e:
            st.error(f"No pude inicializar el cliente de Search Console: {e}")
            st.stop()

# --- Sitio y análisis ---
site_url = pick_site(sc_service)

# --- Paneles de diagnóstico opcionales ---
if st.session_state.get("DEBUG"):
    with st.expander("🔎 Test de prompt (Core Update)", expanded=False):
        st.caption("Comprobá qué prompt se aplicará antes de ejecutar el análisis.")
        if st.button("Probar carga de prompt ahora", key="probe_core"):
            render_prompt_probe(kind="core", force_key="core")
        else:
            st.caption(f"Fuente actual de prompts: {AI_SRC or 'none'}")
        with st.expander("🧪 Diagnóstico Gemini", expanded=False):
            if st.button("Probar SDK Gemini", key="probe_gemini"):
                ok, msgs = gemini_healthcheck()
                st.write("\n".join([f"• {m}" for m in msgs]))
                st.success("Gemini OK" if ok else "Gemini NO está listo (caerá en fallback).")
    render_gsc_filters_debug_panel()

# --- Flujos por análisis ---
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
                sheet_name = meta.get("name", "")
                sheet_url = meta.get("webViewLink") or f"https://docs.google.com/spreadsheets/d/{sid}"
            except Exception:
                sheet_name = ""
                sheet_url = f"https://docs.google.com/spreadsheets/d/{sid}"

            activity_log_append(
                drive_service, gs_client,
                user_email=(_me or {}).get("emailAddress") or "",
                event="analysis",
                site_url=site_url,
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
                sheet_name = meta.get("name", "")
                sheet_url = meta.get("webViewLink") or f"https://docs.google.com/spreadsheets/d/{sid}"
            except Exception:
                sheet_name = ""
                sheet_url = f"https://docs.google.com/spreadsheets/d/{sid}"

            activity_log_append(
                drive_service, gs_client,
                user_email=(_me or {}).get("emailAddress") or "",
                event="analysis",
                site_url=site_url,
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
                sheet_name = meta.get("name", "")
                sheet_url = meta.get("webViewLink") or f"https://docs.google.com/spreadsheets/d/{sid}"
            except Exception:
                sheet_name = ""
                sheet_url = f"https://docs.google.com/spreadsheets/d/{sid}"
            activity_log_append(
                drive_service, gs_client,
                user_email=(_me or {}).get("emailAddress") or "",
                event="analysis",
                site_url=site_url,
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

# --- Panel persistente para generar resumen del último informe ---
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
    st.write("¿Gemini listo?", "GEMINI_API_KEY" in st.secrets or ("gemini" in st.secrets and "api_key" in st.secrets["gemini"]))