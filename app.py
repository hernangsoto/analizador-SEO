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

# ====== Documento de texto ======
from modules.doc_export import (
    create_doc_with_prompt,
    create_doc_from_template_with_content,
    PROMPT_BRANDING_NOMADIC,
)

# ====== Carga de módulos locales ======
from modules.app_config import apply_base_style_and_logo, get_app_home

from modules.app_ext import (
    USING_EXT,
    run_core_update,
    run_evergreen,
    run_traffic_audit,
    run_names_analysis,
    run_discover_snoop,
    run_content_analysis,
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

# Opcionales con fallback limpio
try:
    from modules.app_params import params_for_discover_snoop
except Exception:
    params_for_discover_snoop = lambda: {}

try:
    from modules.app_params import params_for_content
except Exception:
    params_for_content = None

# Runners opcionales (repo externo)
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

# ====== Estilo / branding ======
apply_base_style_and_logo()
try:
    apply_page_style()
except Exception:
    pass

# (CSS, título, login, etc…)
# ============================================================
# ... [todo tu código intermedio intacto: login, selección análisis, runners…]
# ============================================================

# ===== Helper para acciones post-ejecución =====
def show_post_run_actions(gs_client, sheet_id: str, kind: str, site_url: str | None = None):
    import uuid

    st.divider()
    st.subheader("Acciones posteriores")
    st.caption("Elegí qué querés hacer ahora:")

    suffix = f"{kind}_{sheet_id}_{(site_url or 'global').replace('https://','').replace('http://','').replace('/','_')}_{uuid.uuid4().hex[:6]}"

    do_sum = st.checkbox("🤖 Resumen del análisis generado con Nomadic BOT", value=True, key=f"post_sum_{suffix}")
    do_doc = st.checkbox("🤖 Documento de texto basado en el análisis de Nomadic BOT", value=False, key=f"post_doc_{suffix}")
    do_slack = st.checkbox("Resumen del análisis para enviar a Slack (A desarrollar)", value=False, key=f"post_slack_{suffix}")

    if st.button("Ejecutar acciones seleccionadas", type="primary", key=f"post_go_{suffix}"):
        selected = [do_sum, do_doc, do_slack]
        total = sum(1 for x in selected if x)
        if total == 0:
            st.info("Seleccioná al menos una acción."); return

        progress = st.progress(0.0)
        done = 0
        summary_text = st.session_state.get("last_summary_text") or st.session_state.get("gemini_last_text") or ""

        # 1) Resumen IA
        if do_sum:
            with st.spinner("Generando resumen con Nomadic BOT…"):
                try:
                    txt = gemini_summary(gs_client, sheet_id, kind=kind, widget_suffix=f"post_{suffix}") or ""
                    if txt:
                        summary_text = txt
                        st.session_state["last_summary_text"] = txt
                        st.success("Resumen IA generado ✅")
                    else:
                        st.warning("No se obtuvo texto de resumen (vacío).")
                except Exception as e:
                    st.error(f"Falló el resumen IA: {e}")
            done += 1; progress.progress(done/max(total,1))

        if not summary_text:
            summary_text = (
                st.session_state.get("last_summary_text")
                or st.session_state.get("gemini_last_text")
                or ""
            )

        # 2) Documento de texto
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
                        content = summary_text.strip() + f"\n\n—\n➡️ Sheet del análisis: {sheet_url}"
                        title_guess = f"Análisis {kind or ''}".strip() or "Análisis"
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
            done += 1; progress.progress(done/max(total,1))

        # 3) Slack (placeholder)
        if do_slack:
            with st.spinner("Preparando mensaje para Slack…"):
                sheet_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}"
                head = f"*{kind or 'Análisis'}*"
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
            done += 1; progress.progress(done/max(total,1))

        progress.empty()
        st.markdown("### Enlaces")
        st.markdown(f"• **Google Sheets** → https://docs.google.com/spreadsheets/d/{sheet_id}")
        if doc_url:
            st.markdown(f"• **Google Doc** → {doc_url}")


# --- Acciones posteriores (unificadas) ---
if st.session_state.get("last_file_id") and st.session_state.get("last_file_kind"):
    show_post_run_actions(
        gs_client=gs_client,
        sheet_id=st.session_state["last_file_id"],
        kind=st.session_state["last_file_kind"],
        site_url=st.session_state.get("site_url_choice")
    )

# --- Debug info (opcional) ---
if st.session_state.get("DEBUG"):
    st.write(
        "¿Gemini listo?",
        "GEMINI_API_KEY" in st.secrets or (
            "gemini" in st.secrets and "api_key" in st.secrets.get("gemini", {})
        )
    )
