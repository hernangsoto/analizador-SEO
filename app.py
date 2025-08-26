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
    # Evita el warning "Already configured" en reruns o multipágina
    pass

# ------------------------------------------------------------------
# 🔧 Shims de compatibilidad:
# Si moviste archivos a modules/, evita ImportError en módulos que aún
# hacen imports antiguos (app_constants / app_config / app_ext / etc).
# ------------------------------------------------------------------
for _name in [
    "app_constants",
    "app_config",
    "app_ext",
    "app_utils",
    "app_params",
    "app_errors",
    "app_activity",
    "app_auth_flow",
    "app_diagnostics",
    "app_ai",
]:
    try:
        _mod = __import__(f"modules.{_name}", fromlist=["*"])
        sys.modules.setdefault(_name, _mod)
    except Exception:
        pass

# ====== UI / Branding ======
from modules.ui import (
    apply_page_style,  # usado dentro de config
    get_user,
    sidebar_user_info,
    login_screen,
)

# ====== Carga de módulos locales fraccionados ======
from modules.app_config import apply_base_style_and_logo, get_app_home
from modules.app_ext import USING_EXT, run_core_update, run_evergreen, run_traffic_audit, run_names_analysis
from modules.app_utils import get_qp, clear_qp, oauth_flow_store, has_gsc_scope, norm
from modules.app_ai import (
    load_prompts,
    ai_source,
    ai_import_error,
    prompts_map,
    gemini_healthcheck,
    render_prompt_probe,
    gemini_summary,
)
from modules.app_params import (
    params_for_core_update, params_for_evergreen, params_for_auditoria,
    params_for_names,
)
from modules.app_activity import (
    maybe_prefix_sheet_name_with_medio,
    activity_log_append,
)
from modules.app_errors import show_google_error, run_with_indicator
from modules.app_auth_flow import step0_google_identity, logout_screen
from modules.app_diagnostics import scan_repo_for_gsc_and_filters, read_context

# ====== Google modules ya existentes en tu repo ======
from modules.auth import (
    pick_destination_oauth,
    pick_source_oauth,  # puede quedar sin usar si preferimos el flujo robusto
    build_flow,         # necesario para rehidratar Flow en Paso 2 (GSC)
)
from modules.drive import (
    ensure_drive_clients,
    get_google_identity,
    pick_destination,
    share_controls,
)
from modules.gsc import ensure_sc_client
from modules.app_constants import SCOPES_GSC  # scopes de GSC para build_flow

# ====== Estilo / branding ======
apply_base_style_and_logo()
st.title("Analizador SEO 🚀")

# ------------------------------------------------------------
# Diagnóstico de prompts/IA (igual que antes, pero usando helpers nuevos)
# ------------------------------------------------------------
# Cargar prompts al inicio
load_prompts()

if st.session_state.get("DEBUG"):
    import importlib.util
    with st.expander("🧪 Diagnóstico rápido de prompts (opcional)", expanded=False):
        try:
            spec = importlib.util.find_spec("seo_analisis_ext.ai_summaries")
            st.write("Ubicación de seo_analisis_ext.ai_summaries:", getattr(spec, "origin", "(no encontrada)"))
        except Exception:
            st.write("Ubicación de seo_analisis_ext.ai_summaries: (no disponible)")

        st.write("Fuente actual de prompts:", ai_source() or "none")
        err = ai_import_error()
        if err:
            st.warning("Fallo al importar prompts. Ver detalle debajo.")
            with st.expander("Detalle del error de import"):
                st.code(err)

        if st.button("🔁 Reintentar carga de prompts"):
            load_prompts()
            st.rerun()
else:
    # Aviso suave si Gemini no está listo (fuera del panel DEBUG)
    try:
        ok, _ = gemini_healthcheck()
        if not ok:
            st.caption("💡 Podés cargar una API key de Gemini en Secrets para activar resúmenes automáticos (GEMINI_API_KEY o [gemini].api_key).")
    except Exception:
        pass

# === 🔎 Panel de diagnóstico (DEBUG) ===
if st.session_state.get("DEBUG"):
    with st.expander("🛠️ Diagnóstico de filtros de Search Console", expanded=False):
        st.caption("Escanea el código para ubicar dónde llamas a la API de GSC y dónde aplicas filtros por URL (columna 'page').")
        if st.button("Escanear código (GSC + filtros)", key="btn_scan_gsc_files"):
            scan_repo_for_gsc_and_filters()
        results = st.session_state.get("_scan_results", [])
        if results:
            df = pd.DataFrame(results)[["file", "line", "label", "snippet", "pattern"]]
            st.write(f"Coincidencias encontradas: **{len(df)}**")
            st.dataframe(df, use_container_width=True, height=340)
            csv_bytes = df.to_csv(index=False).encode("utf-8")
            st.download_button(
                "⬇️ Descargar CSV de coincidencias",
                data=csv_bytes,
                file_name="diagnostico_gsc_filtros.csv",
                mime="text/csv",
                key="dl_scan_gsc_csv"
            )
            options = [f"{i+1}. {row.file}:{row.line} — {row.label}" for i, row in df.iterrows()]
            sel = st.selectbox("Ver contexto de una coincidencia:", options, index=0, key="sel_scan_item")
            if sel:
                idx = int(sel.split(".")[0]) - 1
                row = df.iloc[idx]
                ctx = read_context(row["file"], int(row["line"]), around=8)
                st.code(ctx, language="python")
        else:
            st.info("Aún no hay resultados. Pulsa **Escanear código (GSC + filtros)** para empezar.")

# ============== App ==============

APP_HOME = get_app_home()

# Detectar pantalla de logout por query param
_view = get_qp().get("view")
if isinstance(_view, list):
    _view = _view[0] if _view else None
if _view == "logout":
    logout_screen(APP_HOME)
    st.stop()

# Preferir Paso 0 (OIDC) si así se indica en secrets
prefer_oidc = bool(st.secrets.get("auth", {}).get("prefer_oidc", True))

# 1) Identidad Google ya guardada?
ident = st.session_state.get("_google_identity")

# 2) Usuario de Streamlit
user = get_user()

# 3) Si había bypass activo y preferimos OIDC, lo limpiamos para mostrar Paso 0
if prefer_oidc and st.session_state.get("_auth_bypass"):
    st.session_state.pop("_auth_bypass", None)
    user = None

# 4) Mostrar Paso 0 si prefer_oidc y aún no hay identidad
if prefer_oidc and not ident:
    ident = step0_google_identity()
    if not ident:
        st.stop()

# 5) Si no hay user de Streamlit, crear sintético con la identidad OIDC
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

# Sidebar → Mantenimiento
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
        # 📝 Log: login (Drive/Sheets listo)
        activity_log_append(
            drive_service, gs_client,
            user_email=email_txt, event="login",
            gsc_account=st.session_state.get("src_account_label") or "",
            notes="OIDC + Drive/Sheets listos"
        )
    except Exception as e:
        st.error(f"No pude inicializar Drive/Sheets con la cuenta PERSONAL: {e}")
        st.stop()

# --- PASO 2: Carpeta destino (opcional) en expander ---
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

# ========== NUEVO ORDEN: Elegir análisis ANTES de Search Console ==========
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

                # 📝 Log
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
                    site_url="",  # no aplica
                    analysis_kind="Nombres (KG+Wikipedia)",
                    sheet_id=sid, sheet_name=sheet_name, sheet_url=sheet_url,
                    gsc_account="",  # no aplica
                    notes=f"lang={params_names.get('lang')}, n={total}"
                )

                st.session_state["last_file_id"] = sid
                st.session_state["last_file_kind"] = "names"
                gemini_summary(gs_client, sid, kind="names", widget_suffix="after_run")

    # Panel persistente de resumen si ya hay algo
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
    st.stop()  # fin rama nombres

# ======== Resto de análisis (sí requieren GSC) ========

# --- Helper: hint para CSRF/state mismatch en Paso 2 ---
def _csrf_mismatch_hint(step_label: str = "Paso 2"):
    st.error("CSRF Warning: el 'state' devuelto no coincide con el generado.")
    st.info(f"Hacé clic en **Reiniciar {step_label}** y repetí la autorización (un solo click).")
    if st.button(f"Reiniciar {step_label}", key="btn_restart_step2"):
        for k in ("creds_src", "oauth_src", "step3_done", "src_account_label"):
            st.session_state.pop(k, None)
        clear_qp()
        st.rerun()

# --- Helpers de Paso 2 (GSC) para flujo robusto ---
def _append_hd(auth_url: str, domain: str = "nomadic.agency") -> str:
    sep = "&" if "?" in auth_url else "?"
    return f"{auth_url}{sep}hd={domain}"

def step2_connect_gsc(sc_choice: str):
    """
    Flujo robusto para conectar la cuenta de Search Console (Acceso / Acceso Medios),
    con rehidratación de Flow si cambia el 'state' en la redirección.
    Devuelve un objeto Credentials o None si el usuario aún no finalizó.
    """
    # Si ya hay credenciales listas, devolvelas
    if st.session_state.get("step3_done") and st.session_state.get("creds_src"):
        try:
            return Credentials(**st.session_state["creds_src"])
        except Exception:
            pass  # si fallan, seguimos abajo para regenerar

    # Construcción/reuso del flujo OAuth para la cuenta elegida
    store = oauth_flow_store()
    oo = st.session_state.get("oauth_src")

    # Si cambió la cuenta o no hay sesión, empezamos limpio
    if not oo or (oo.get("account") != sc_choice):
        st.session_state.pop("oauth_src", None)

        # Armamos el Flow con el client correspondiente a sc_choice
        flow = build_flow(sc_choice, SCOPES_GSC)
        # Armamos la URL de autorización
        auth_url, state = flow.authorization_url(
            prompt="consent select_account",
            access_type="offline",
            include_granted_scopes="true",
        )
        auth_url = _append_hd(auth_url)

        # Detección simple del modo: redirect si no es localhost manual
        redirect_uri = getattr(flow, "redirect_uri", "") or ""
        use_redirect = not redirect_uri.startswith("http://localhost")

        st.session_state["oauth_src"] = {
            "account": sc_choice,
            "flow_state": state,
            "use_redirect": use_redirect,
            "redirect_uri": redirect_uri or "http://localhost",
            "auth_url": auth_url,
        }
        store[state] = {"flow": flow, "created": time.time(), "mode": "web" if use_redirect else "installed"}
        oo = st.session_state["oauth_src"]

    # Si estamos en retorno de OAuth (hay ?code=...), intentamos rehidratar y canjear token
    qp = get_qp()
    code = qp.get("code", [None])[0] if isinstance(qp.get("code"), list) else qp.get("code")
    state_in = qp.get("state", [None])[0] if isinstance(qp.get("state"), list) else qp.get("state")

    if oo.get("use_redirect") and code:
        expected_state = oo.get("flow_state")
        flow = None

        # 1) Usar Flow guardado con la clave 'state' que vino en la URL
        if state_in and state_in in store:
            flow = store.pop(state_in)["flow"]

        # 2) Si no está en memoria, re-crear con el state recibido (rehidratación)
        if not flow:
            try:
                flow = build_flow(sc_choice, SCOPES_GSC)
                try:
                    setattr(flow, "_state", state_in)  # best-effort
                except Exception:
                    pass
            except Exception as e:
                st.error(f"No pude reconstruir el flujo OAuth: {e}")
                st.stop()

        # 3) Avisar si el state difiere (pestaña nueva / rerun), pero continuar
        if expected_state and state_in and state_in != expected_state:
            st.info("Aviso: el 'state' no coincide (posible nueva pestaña). Continuando con el flujo rehidratado…")

        # 4) Intercambiar código por tokens
        from urllib.parse import urlencode
        current_url = f"{oo['redirect_uri']}?{urlencode({k: (v[0] if isinstance(v, list) else v) for k, v in qp.items()}, doseq=True)}"
        try:
            flow.fetch_token(authorization_response=current_url)
            creds = flow.credentials
            # Persistimos en session_state
            st.session_state["creds_src"] = {
                "token": creds.token,
                "refresh_token": getattr(creds, "refresh_token", None),
                "token_uri": creds.token_uri,
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "scopes": creds.scopes,
            }
            st.session_state["src_account_label"] = sc_choice
            st.session_state["step3_done"] = True
            clear_qp()
            return creds
        except Exception as e:
            msg = str(e)
            if "csrf" in msg.lower() or "state" in msg.lower() or "mismatch" in msg.lower():
                _csrf_mismatch_hint("Paso 2")
                st.stop()
            st.error(f"No se pudo completar la autorización de Search Console: {e}")
            st.stop()

    # UI: seguimos en fase previa a retorno (primera vez)
    if oo.get("use_redirect"):
        try:
            st.link_button(f"Conectar cuenta ({sc_choice})", oo["auth_url"])
        except Exception:
            st.markdown(
                f'<a href="{oo["auth_url"]}" target="_self" rel="noopener" '
                f'style="display:inline-block;padding=.6rem 1rem;border-radius:8px;'
                f'background:#8e7cc3;color:#fff;text-decoration:none;font-weight:600;">'
                f'Conectar cuenta ({sc_choice})</a>',
                unsafe_allow_html=True
            )
        st.caption("Tras autorizar, volverás automáticamente a esta app.")
        st.stop()
    else:
        # Modo manual (installed)
        st.info(f"Modo manual activo para **{sc_choice}**.")
        st.markdown(f"🔗 **Paso A (GSC)**: [Abrir autorización]({oo['auth_url']})")
        with st.expander("Ver/copiar URL de autorización (GSC)"):
            st.code(oo["auth_url"])
        raw = st.text_input(
            "🔑 Paso B (GSC): pegá la URL completa (http://localhost/?code=...&state=...)",
            key="auth_response_url_gsc",
            placeholder="http://localhost/?code=...&state=..."
        )
        if st.button("Conectar cuenta (GSC)", type="primary", key="btn_src_connect"):
            if not raw.strip():
                st.error("Pegá la URL completa de redirección (incluye code y state).")
                st.stop()

            try:
                from urllib.parse import urlparse, parse_qs
                q = parse_qs(urlparse(raw).query)
                state_in_manual = q.get("state", [None])[0]

                flow = None
                # Priorizar Flow guardado por el state pegado
                if state_in_manual and state_in_manual in store:
                    flow = store.pop(state_in_manual)["flow"]
                if not flow:
                    # Re-crear y setear state best-effort
                    flow = build_flow(sc_choice, SCOPES_GSC)
                    try:
                        setattr(flow, "_state", state_in_manual or oo.get("flow_state"))
                    except Exception:
                        pass

                flow.fetch_token(authorization_response=raw)
                creds = flow.credentials
                st.session_state["creds_src"] = {
                    "token": creds.token,
                    "refresh_token": getattr(creds, "refresh_token", None),
                    "token_uri": creds.token_uri,
                    "client_id": creds.client_id,
                    "client_secret": creds.client_secret,
                    "scopes": creds.scopes,
                }
                st.session_state["src_account_label"] = sc_choice
                st.session_state["step3_done"] = True
                clear_qp()
                return creds
            except Exception as e:
                msg = str(e)
                if "csrf" in msg.lower() or "state" in msg.lower() or "mismatch" in msg.lower():
                    _csrf_mismatch_hint("Paso 2")
                    st.stop()
                st.error(f"No se pudo completar la autorización GSC (manual): {e}")
                st.stop()

    return None  # si aún no terminó

# --- PASO 3: Conectar Search Console (fuente de datos) ---
sc_service = None

st.subheader("Selecciona la cuenta con acceso a Search Console")
account_options = ["Acceso", "Acceso Medios", "Acceso en cuenta personal de Nomadic"]
_default_label = st.session_state.get("sc_account_choice", "Acceso en cuenta personal de Nomadic")
default_idx = account_options.index(_default_label) if _default_label in account_options else 2

sc_choice = st.selectbox(
    "Elegí la cuenta para consultar datos de Search Console",
    account_options, index=default_idx, key="sc_account_choice"
)

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
                clear_qp()
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
            f'''
            <div class="success-inline">
                Cuenta de acceso (Search Console): <strong>Acceso en cuenta personal de Nomadic</strong>
                <a href="{APP_HOME}?action=change_src" target="_self" rel="nofollow">(Cambiar cuenta de acceso)</a>
            </div>
            ''',
            unsafe_allow_html=True
        )
    except Exception as e:
        st.error(f"No pude inicializar Search Console con la cuenta personal: {e}")
        st.stop()
else:
    # Flujo robusto para "Acceso" / "Acceso Medios"
    creds_src_obj = step2_connect_gsc(sc_choice)
    if not creds_src_obj:
        st.stop()
    try:
        sc_service = ensure_sc_client(creds_src_obj)
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

# --- PASO 4: sitio + PASO 5: análisis ---
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
        st.error("No se encontraron sitios verificados en esta cuenta.")
        st.stop()
    options = sorted({s["siteUrl"] for s in verified})
    prev = st.session_state.get("site_url_choice")
    index = options.index(prev) if prev in options else 0
    site_url = st.selectbox("Sitio verificado:", options, index=index, key="site_url_choice")
    return site_url

site_url = pick_site(sc_service)

# ============== Flujos por análisis que requieren GSC ==============
if analisis == "4":
    if run_core_update is None:
        st.warning("Este despliegue no incluye run_core_update.")
    else:
        params = params_for_core_update()

        if st.session_state.get("DEBUG"):
            with st.expander("🔎 Test de prompt (Core Update)", expanded=True):
                st.caption("Comprobá qué prompt se aplicará antes de ejecutar el análisis.")
                if st.button("Probar carga de prompt ahora", key="probe_core"):
                    render_prompt_probe(kind="core", force_key="core")
                else:
                    st.caption(f"Fuente actual de prompts: {ai_source()}")
                with st.expander("🧪 Diagnóstico Gemini", expanded=False):
                    if st.button("Probar SDK Gemini", key="probe_gemini"):
                        ok, msgs = gemini_healthcheck()
                        st.write("\n".join([f"• {m}" for m in msgs]))
                        if ok:
                            st.success("Gemini OK: el resumen con prompt debería funcionar.")
                        else:
                            st.error("Gemini no está listo: se caerá al fallback.")

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

            # 📝 Log: análisis Core Update
            try:
                meta = drive_service.files().get(fileId=sid, fields="name,webViewLink").execute()
                sheet_name = meta.get("name", "")
                sheet_url = meta.get("webViewLink") or f"https://docs.google.com/spreadsheets/d/{sid}"
            except Exception:
                sheet_name = ""
                sheet_url = f"https://docs.google.com/spreadsheets/d/{sid}"
            activity_log_append(
                drive_service, gs_client,
                user_email=( _me or {}).get("emailAddress") or "",
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

            # 📝 Log: análisis Evergreen
            try:
                meta = drive_service.files().get(fileId=sid, fields="name,webViewLink").execute()
                sheet_name = meta.get("name", "")
                sheet_url = meta.get("webViewLink") or f"https://docs.google.com/spreadsheets/d/{sid}"
            except Exception:
                sheet_name = ""
                sheet_url = f"https://docs.google.com/spreadsheets/d/{sid}"
            activity_log_append(
                drive_service, gs_client,
                user_email=( _me or {}).get("emailAddress") or "",
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

            # 📝 Log: análisis Auditoría
            try:
                meta = drive_service.files().get(fileId=sid, fields="name,webViewLink").execute()
                sheet_name = meta.get("name", "")
                sheet_url = meta.get("webViewLink") or f"https://docs.google.com/spreadsheets/d/{sid}"
            except Exception:
                sheet_name = ""
                sheet_url = f"https://docs.google.com/spreadsheets/d/{sid}"
            activity_log_append(
                drive_service, gs_client,
                user_email=( _me or {}).get("emailAddress") or "",
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

# --- Panel persistente para generar resumen del último informe sin rerun del análisis ---
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

# Debug opcional (solo si está activo)
if st.session_state.get("DEBUG"):
    st.write(
        "¿Gemini listo?",
        "GEMINI_API_KEY" in st.secrets or ("gemini" in st.secrets and "api_key" in st.secrets.get('gemini', {}))
    )