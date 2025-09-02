from __future__ import annotations

# --- Permisos OAuth en localhost + tolerancia de scope
import os
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

import sys
import json
from types import SimpleNamespace
from datetime import date, timedelta
from urllib.parse import urlparse

import pandas as pd
import streamlit as st
from google.oauth2.credentials import Credentials
from concurrent.futures import ThreadPoolExecutor, as_completed

# ====== Config base ======
try:
    st.set_page_config(layout="wide", page_title="Análisis SEO", page_icon="📊")
except Exception:
    pass

# ---- Shims de compatibilidad (si faltan módulos externos, la app sigue)
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
from modules.ui import apply_page_style, get_user, sidebar_user_info, login_screen  # type: ignore

# ====== Carga de módulos locales ======
from modules.app_config import apply_base_style_and_logo, get_app_home  # type: ignore
from modules.app_ext import USING_EXT, run_core_update, run_evergreen, run_traffic_audit, run_names_analysis  # type: ignore

# Discover Snoop (repo externo) — proteger import por si no está disponible
try:
    from modules.app_ext import run_discover_snoop  # type: ignore
except Exception:
    run_discover_snoop = None  # fallback

from modules.app_utils import get_qp, clear_qp, has_gsc_scope, norm  # type: ignore
from modules.app_ai import load_prompts, gemini_healthcheck, gemini_summary  # type: ignore
from modules.app_params import (
    params_for_core_update, params_for_evergreen, params_for_auditoria, params_for_names,  # type: ignore
)

from modules.app_activity import maybe_prefix_sheet_name_with_medio, activity_log_append  # type: ignore
from modules.app_errors import run_with_indicator  # type: ignore
from modules.app_auth_flow import step0_google_identity, logout_screen  # type: ignore
from modules.app_diagnostics import scan_repo_for_gsc_and_filters, read_context  # type: ignore

# 🔑 para leer tokens guardados por el Paso 0 en otra pestaña
from modules.utils import token_store  # type: ignore

# ====== Google modules ======
from modules.drive import ensure_drive_clients, get_google_identity, pick_destination, share_controls  # type: ignore
from modules.gsc import ensure_sc_client  # type: ignore

# ====== Estilo / branding ======
apply_base_style_and_logo()

# CSS compacto
st.markdown("""
<style>
div[data-testid="stAppViewContainer"] { padding-top: 0 !important; }
main .block-container { margin-top: 0 !important; padding-top: .5rem !important; }
header[data-testid="stHeader"] { z-index: 1500 !important; }
.small-note { color:#666; font-size:.9rem; }
.success-inline { background:#eefaf1; border:1px solid #cdecd6; padding:.5rem .75rem; border-radius:.5rem; }
.warning-inline { background:#fff7e6; border:1px solid #fde3a7; padding:.5rem .75rem; border-radius:.5rem; }
hr{margin:.75rem 0}
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
    # Deshabilitamos el externo y usamos el modo 'ligero' propio en 9.
    opciones.append("9. Análisis de contenido (ligero) ✅")

    key = st.radio("Tipos disponibles:", opciones, index=3, key="analysis_choice")
    if key.startswith("4."): return "4"
    if key.startswith("5."): return "5"
    if key.startswith("6."): return "6"
    if key.startswith("7."): return "7"
    if key.startswith("9."): return "9"
    return "0"

analisis = pick_analysis(include_auditoria, include_names=True, include_content=True)

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
# AYUDAS / Utilidades
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
    try:
        body = {
            "startDate": str(start),
            "endDate": str(end),
            "dimensions": ["page"],
            "rowLimit": int(row_limit),
            "startRow": 0,
            "type": search_type,  # "web" | "discover"
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
        st.session_state["_lite_error"] = f"GSC query error ({search_type}): {e}"
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
    if u.endswith((".jpg",".jpeg",".png",".gif",".svg",".webp",".mp4",".mp3",".m3u8",".pdf")):
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

def _norm_space(s: str) -> str:
    return " ".join((s or "").split())

def _join_list(vals: list[str], sep: str) -> str:
    vals = [ _norm_space(x) for x in (vals or []) if _norm_space(x) ]
    return sep.join(vals)

def _domain_from_site(site_url: str) -> str:
    try:
        netloc = urlparse(site_url).netloc.replace("www.","")
        return netloc or "sitio"
    except Exception:
        return "sitio"

def _create_or_move_sheet(gs_client, drive_service, title: str, folder_id: str | None) -> str:
    # Crear en raíz
    sh = gs_client.create(title)
    sid = sh.id
    # Mover si corresponde
    if folder_id:
        try:
            cur = drive_service.files().get(fileId=sid, fields="parents").execute()
            cur_parents = ",".join(cur.get("parents", []))
            drive_service.files().update(
                fileId=sid,
                addParents=folder_id,
                removeParents=cur_parents
            ).execute()
        except Exception:
            pass
    return sid

# =========================
# 4/5/6 (otros análisis con runners externos)
# =========================
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

# =========================
# 9) Análisis de contenido (LIGERO - sin JSON ni runner externo)
# =========================
elif analisis == "9":
    st.subheader("Análisis de contenido (ligero)")

    # === Ventana de fechas (Desde/Hasta) y origen ===
    lag_days = int(st.secrets.get("defaults", {}).get("lag_days", 3))
    today = date.today()
    default_end = today - timedelta(days=lag_days)
    default_start = default_end - timedelta(days=27)

    c1, c2, c3 = st.columns([1,1,1.2])
    with c1:
        start_date = st.date_input("Desde (fecha)", value=default_start, key="lite_start")
    with c2:
        end_date = st.date_input("Hasta (fecha)", value=default_end, key="lite_end")
    with c3:
        origen = st.selectbox("Origen", ["Search", "Discover", "Search + Discover"], index=0, key="lite_src")

    # Máximo URLs por origen
    max_urls = st.number_input("Máximo de URLs por origen", min_value=10, max_value=5000, value=300, step=10, key="lite_max_urls")

    # === Opciones avanzadas ===
    with st.expander("⚙️ Opciones avanzadas de filtrado", expanded=False):
        c1, c2, c3 = st.columns([1,1,1])
        with c1:
            country = st.text_input("País (ISO-3, ej. ARG, MEX, USA)", value="", key="lite_country")
            order_by = st.selectbox("Ordenar por", ["clicks","impressions","ctr","position"], index=0, key="lite_order")
        with c2:
            device = st.selectbox("Dispositivo", ["(cualquiera)","Desktop","Mobile","Tablet"], index=0, key="lite_device")
            min_clicks = st.number_input("Mínimo de clics", min_value=0, value=0, step=10, key="lite_min_clicks")
        with c3:
            min_impr = st.number_input("Mínimo de impresiones", min_value=0, value=0, step=100, key="lite_min_impr")
            only_articles = st.checkbox("Solo artículos (filtra players/tags/etc.)", value=True, key="lite_only_articles")

        st.markdown("---")
        c4, c5, c6, c7 = st.columns([1,1,2,1])
        with c4:
            concurrency = st.number_input("Concurrencia (hilos)", min_value=1, max_value=64, value=8, step=1, key="lite_workers")
        with c5:
            timeout = st.number_input("Timeout por página (seg)", min_value=3, max_value=60, value=12, step=1, key="lite_timeout")
        with c6:
            ua = st.text_input("User-Agent (opcional)", value="", key="lite_ua")
            if not ua.strip():
                st.caption("Sugerencia UA por defecto:")
                st.code(_suggest_user_agent(ua))
        with c7:
            list_sep = st.text_input("Separador para listas", value=" | ", key="lite_sep")

    # === Campos a extraer (uno debajo del otro, con XPaths cuando corresponda) ===
    st.markdown("### 🧲 Campos a extraer")

    # Helper para fila (checkbox + xpath)
    def field_row(label: str, key_chk: str, default=True, xpath=False, key_xpath: str | None=None, placeholder="//xpath/..."):
        cols = st.columns([1.2, 2.2]) if xpath else st.columns([1, 1])
        checked = cols[0].checkbox(label, value=default, key=key_chk)
        xp_val = ""
        if xpath:
            xp_val = cols[1].text_input(" ", value=st.session_state.get(key_xpath, ""), key=key_xpath, placeholder=placeholder, label_visibility="collapsed")
        return checked, xp_val

    # Fila por fila
    want_title, _ = field_row("Title (<title>)", "f_title", default=True, xpath=False)
    want_h1, xp_h1 = field_row("H1 (XPath opcional)", "f_h1", default=True, xpath=True, key_xpath="xp_h1", placeholder="//h1 | //*[@id='titulo']//h1")
    want_ogt, xp_og = field_row("OG Title (XPath opcional)", "f_og", default=True, xpath=True, key_xpath="xp_og", placeholder="//meta[@property='og:title']/@content")
    want_desc, xp_desc = field_row("Description (meta) (XPath opcional)", "f_desc", default=True, xpath=True, key_xpath="xp_desc", placeholder="//meta[@name='description']/@content")

    want_author, xp_author = field_row("Autor (XPath)", "f_author", default=True, xpath=True, key_xpath="xp_author", placeholder="//*[@itemprop='author'] | //meta[@name='author']/@content")
    want_updated, xp_updated = field_row("Fecha de actualización (XPath)", "f_updated", default=False, xpath=True, key_xpath="xp_updated", placeholder="//meta[@property='article:modified_time']/@content | //time[@itemprop='dateModified']/@datetime")

    want_first_p, xp_first_p = field_row("Primer párrafo (XPath)", "f_firstp", default=True, xpath=True, key_xpath="xp_firstp", placeholder="//article//p[1] | //*[@id='cuerpo']//p[1]")

    want_article_container, xp_article = field_row("Texto del artículo (contenedor XPath)", "f_article", default=True, xpath=True, key_xpath="xp_article", placeholder="//article | //*[@id='cuerpo']")

    want_h2, _ = field_row("H2 dentro del artículo (lista)", "f_h2", default=True, xpath=False)
    want_h3, _ = field_row("H3 dentro del artículo (lista)", "f_h3", default=False, xpath=False)

    want_bolds, _ = field_row("Negritas dentro del artículo (lista y cantidad)", "f_bolds", default=False, xpath=False)
    want_links, _ = field_row("Links dentro del artículo (anchors y cantidad)", "f_links", default=False, xpath=False)

    want_tags, xp_tags = field_row("Tags (XPath de los elementos)", "f_tags", default=False, xpath=True, key_xpath="xp_tags", placeholder="//*[@class='tags']//a | //ul[@class='tags']//li")

    want_rel_box, xp_rel = field_row("Caja de noticias relacionadas (XPath contenedor)", "f_rel", default=False, xpath=True, key_xpath="xp_rel", placeholder="//*[@class='related'] | //aside[contains(@class,'relacionadas')]")

    # Advertencia si se piden campos que dependen del contenedor del artículo
    depends_container = any([want_h2, want_h3, want_bolds, want_links]) and not xp_article.strip()
    if depends_container:
        st.markdown('<div class="warning-inline">⚠️ Seleccionaste campos que deben buscarse dentro del artículo, pero no definiste el XPath del contenedor. Esos campos podrían salir vacíos.</div>', unsafe_allow_html=True)

    # =========================
    # PREVIEW DE SEMILLAS (GSC)
    # =========================
    st.markdown("### 🔎 Semillas desde Search Console")

    seeds_search = []
    seeds_discover = []

    src_mode = "web" if origen == "Search" else ("discover" if origen == "Discover" else "both")

    if src_mode in ("web","both"):
        seeds_search = _gsc_fetch_top_urls(
            sc_service, site_url, start_date, end_date, "web",
            country or None if not country else country,
            None if device == "(cualquiera)" else device,
            order_by, row_limit=max_urls
        )
        st.write(f"**Search**: {len(seeds_search):,} filas")
        st.code([r["page"] for r in seeds_search[:10]])
    if src_mode in ("discover","both"):
        seeds_discover = _gsc_fetch_top_urls(
            sc_service, site_url, start_date, end_date, "discover",
            country or None if not country else country,
            None if device == "(cualquiera)" else device,
            order_by, row_limit=max_urls
        )
        st.write(f"**Discover**: {len(seeds_discover):,} filas")
        st.code([r["page"] for r in seeds_discover[:10]])

    df_seeds = pd.DataFrame([], columns=["page","clicks","impressions","ctr","position"])
    if seeds_search:
        a = pd.DataFrame(seeds_search); a["source"] = "Search"
        df_seeds = pd.concat([df_seeds, a], ignore_index=True)
    if seeds_discover:
        b = pd.DataFrame(seeds_discover); b["source"] = "Discover"
        df_seeds = pd.concat([df_seeds, b], ignore_index=True)

    before_count = len(df_seeds)
    if before_count == 0:
        st.error("No hay semillas para la ventana/configuración elegida.")
        st.stop()

    # Umbrales
    if min_clicks > 0:
        df_seeds = df_seeds[df_seeds["clicks"] >= min_clicks]
    if min_impr > 0:
        df_seeds = df_seeds[df_seeds["impressions"] >= min_impr]

    # Filtro artículos
    urls_all = df_seeds["page"].dropna().astype(str).tolist()
    urls_filtered = _filter_article_urls(urls_all) if only_articles else urls_all
    st.caption(f"URLs candidatas: {len(urls_all):,}  →  después del filtro artículos: {len(urls_filtered):,}")

    # Armar lista final (con métrica por URL)
    df_seeds = df_seeds[df_seeds["page"].isin(urls_filtered)].copy()
    df_seeds.reset_index(drop=True, inplace=True)

    # =========================
    # SCRAPING helpers
    # =========================
    try:
        import requests
        from bs4 import BeautifulSoup
        from lxml import html as lxml_html  # para XPaths
    except Exception as e:
        st.error(f"No pude cargar dependencias de scraping (requests/bs4/lxml): {e}")
        st.stop()

    HEADERS = {"User-Agent": _suggest_user_agent(ua)}

    def _first_or_text(vals, join_with=" "):
        if not vals: return ""
        if isinstance(vals, (list, tuple)):
            vals = [v.strip() for v in vals if isinstance(v, str)]
            return _norm_space(join_with.join(vals))
        return _norm_space(str(vals))

    def _xpath_text(tree, xp: str) -> str:
        try:
            got = tree.xpath(xp)
            if not got: return ""
            # Si devuelve atributos/strings:
            if all(isinstance(g, (str, bytes)) for g in got):
                vals = [g.decode("utf-8") if isinstance(g, bytes) else g for g in got]
                return _first_or_text(vals)
            # Nodos: concatenar texto
            vals = []
            for node in got:
                try:
                    vals.append(node.text_content())
                except Exception:
                    vals.append(str(node))
            return _first_or_text(vals)
        except Exception:
            return ""

    def _extract_article_scoped(tree, container_xp: str):
        container = None
        try:
            if container_xp.strip():
                nodes = tree.xpath(container_xp)
                container = nodes[0] if nodes else None
        except Exception:
            container = None
        return container

    def _scrape_one(url: str) -> dict:
        out = {
            "url": url, "error": "",

            # básicos
            "title": "", "h1": "", "og_title": "", "meta_description": "",

            # autor/fecha
            "author": "", "updated": "",

            # artículo
            "first_paragraph": "", "h2_list": "", "h3_list": "",
            "bolds_count": "", "bolds_list": "",
            "links_count": "", "links_anchors": "",

            # tags
            "tags": "",

            # relacionadas
            "rel_links_count": "", "rel_anchors": "", "rel_urls": "",
        }
        try:
            rs = requests.get(url, headers=HEADERS, timeout=timeout)
            rs.raise_for_status()
            html_text = rs.text
            soup = BeautifulSoup(html_text, "html.parser")
            try:
                tree = lxml_html.fromstring(html_text)
            except Exception:
                tree = None

            # Title
            if want_title:
                ti = (soup.title.string if soup.title and soup.title.string else "").strip()
                out["title"] = _norm_space(ti)

            # H1
            if want_h1:
                val = ""
                if xp_h1 and tree is not None:
                    val = _xpath_text(tree, xp_h1)
                if not val:
                    h1 = soup.select_one("h1")
                    if h1: val = _norm_space(h1.get_text(" ", strip=True))
                out["h1"] = val

            # OG title
            if want_ogt:
                val = ""
                if xp_og and tree is not None:
                    val = _xpath_text(tree, xp_og)
                if not val:
                    og = soup.find("meta", attrs={"property":"og:title"})
                    if og: val = og.get("content","").strip()
                out["og_title"] = _norm_space(val)

            # Description
            if want_desc:
                val = ""
                if xp_desc and tree is not None:
                    val = _xpath_text(tree, xp_desc)
                if not val:
                    m = soup.find("meta", attrs={"name":"description"})
                    if m: val = m.get("content","").strip()
                out["meta_description"] = _norm_space(val)

            # Autor
            if want_author:
                val = ""
                if xp_author and tree is not None:
                    val = _xpath_text(tree, xp_author)
                if not val:
                    m = soup.find("meta", attrs={"name":"author"})
                    if m: val = m.get("content","").strip()
                    if not val:
                        cand = soup.select("[itemprop='author'], [rel='author'], .author, [class*='author']")
                        if cand:
                            val = _norm_space(cand[0].get_text(" ", strip=True))
                out["author"] = _norm_space(val)

            # Updated
            if want_updated:
                val = ""
                if xp_updated and tree is not None:
                    val = _xpath_text(tree, xp_updated)
                if not val:
                    m = soup.find("meta", attrs={"property":"article:modified_time"})
                    if m: val = m.get("content","").strip()
                    if not val:
                        t = soup.find("time", attrs={"itemprop":"dateModified"})
                        if t: val = t.get("datetime") or t.get_text(" ", strip=True)
                out["updated"] = _norm_space(val)

            # Primer párrafo
            if want_first_p:
                val = ""
                if xp_first_p and tree is not None:
                    val = _xpath_text(tree, xp_first_p)
                if not val and tree is not None and xp_article:
                    try:
                        cont_nodes = tree.xpath(xp_article)
                        if cont_nodes:
                            p = cont_nodes[0].xpath(".//p[1]")
                            if p:
                                val = _norm_space(p[0].text_content())
                    except Exception:
                        pass
                if not val:
                    # heurística
                    p = soup.select_one("article p, main p, .article p, #cuerpo p")
                    if p: val = _norm_space(p.get_text(" ", strip=True))
                out["first_paragraph"] = val

            # Ámbito artículo (para h2/h3/bolds/links)
            container = None
            if xp_article and tree is not None:
                container = _extract_article_scoped(tree, xp_article)

            if want_h2:
                items = []
                if container is not None:
                    try:
                        nodes = container.xpath(".//h2")
                        items = [_norm_space(n.text_content()) for n in nodes]
                    except Exception:
                        items = []
                out["h2_list"] = _join_list(items, list_sep)

            if want_h3:
                items = []
                if container is not None:
                    try:
                        nodes = container.xpath(".//h3")
                        items = [_norm_space(n.text_content()) for n in nodes]
                    except Exception:
                        items = []
                out["h3_list"] = _join_list(items, list_sep)

            if want_bolds:
                items = []
                if container is not None:
                    try:
                        nodes = container.xpath(".//b|.//strong")
                        items = [_norm_space(n.text_content()) for n in nodes]
                    except Exception:
                        items = []
                out["bolds_list"] = _join_list(items, list_sep)
                out["bolds_count"] = str(len([x for x in items if x]))

            if want_links:
                anchors = []
                if container is not None:
                    try:
                        nodes = container.xpath(".//a")
                        anchors = [_norm_space(n.text_content()) for n in nodes]
                    except Exception:
                        anchors = []
                out["links_anchors"] = _join_list(anchors, list_sep)
                out["links_count"] = str(len([x for x in anchors if x]))

            # Tags
            if want_tags:
                vals = []
                if xp_tags and tree is not None:
                    try:
                        nodes = tree.xpath(xp_tags)
                        for n in nodes:
                            if isinstance(n, str):
                                vals.append(_norm_space(n))
                            else:
                                try:
                                    vals.append(_norm_space(n.text_content()))
                                except Exception:
                                    pass
                    except Exception:
                        vals = []
                out["tags"] = _join_list(vals, list_sep)

            # Caja relacionadas
            if want_rel_box:
                count = 0
                anchors, urls_ = [], []
                if xp_rel and tree is not None:
                    try:
                        conts = tree.xpath(xp_rel)
                        if conts:
                            # si son varios contenedores, concatenar
                            for c in conts:
                                try:
                                    links = c.xpath(".//a")
                                    for a in links:
                                        anchors.append(_norm_space(a.text_content()))
                                        try:
                                            hrefs = a.xpath(".//@href")
                                            if hrefs:
                                                urls_.append(hrefs[0])
                                        except Exception:
                                            pass
                                except Exception:
                                    pass
                    except Exception:
                        pass
                count = len([x for x in anchors if x])
                out["rel_links_count"] = str(count)
                out["rel_anchors"] = _join_list(anchors, list_sep)
                out["rel_urls"] = _join_list(urls_, list_sep)

        except Exception as e:
            out["error"] = str(e)

        return out

    # =========================
    # Ejecutar scraping
    # =========================
    st.markdown("### 🧪 Ejecutar")
    can_run = len(df_seeds) > 0
    if st.button("📰 Ejecutar scraping y publicar en Sheets", type="primary", disabled=not can_run, key="btn_lite_run"):
        rows = []
        urls = df_seeds["page"].tolist()
        srcs = df_seeds["source"].tolist()
        clicks = df_seeds["clicks"].tolist()
        imprs = df_seeds["impressions"].tolist()
        ctrs = df_seeds["ctr"].tolist()

        progress = st.progress(0.0)
        collected = {}
        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            fut_map = {ex.submit(_scrape_one, u): i for i, u in enumerate(urls)}
            done_n = 0
            for fut in as_completed(fut_map):
                idx = fut_map[fut]
                try:
                    collected[idx] = fut.result()
                except Exception as e:
                    collected[idx] = {"url": urls[idx], "error": str(e)}
                done_n += 1
                progress.progress(done_n / max(1, len(urls)))

        # Construir DataFrame final
        base_cols = ["URL","Fuente","Clicks","Impresiones","CTR"]
        extra_cols = []
        if want_title: extra_cols.append("Title")
        if want_h1: extra_cols.append("H1")
        if want_ogt: extra_cols.append("OG_Title")
        if want_desc: extra_cols.append("Description")
        if want_author: extra_cols.append("Autor")
        if want_updated: extra_cols.append("Fecha_Actualizacion")
        if want_first_p: extra_cols.append("Primer_Parrafo")
        if want_h2: extra_cols.append("H2_list")
        if want_h3: extra_cols.append("H3_list")
        if want_bolds:
            extra_cols += ["Negritas_cantidad","Negritas_lista"]
        if want_links:
            extra_cols += ["Links_cantidad","Links_anchors"]
        if want_tags: extra_cols.append("Tags")
        if want_rel_box:
            extra_cols += ["Rel_links_cantidad","Rel_anchors","Rel_urls"]
        extra_cols.append("Scrape_error")

        data_out = []
        for i in range(len(urls)):
            info = collected.get(i, {})
            row = [
                urls[i],
                srcs[i] if i < len(srcs) else "",
                clicks[i] if i < len(clicks) else "",
                imprs[i] if i < len(imprs) else "",
                ctrs[i] if i < len(ctrs) else "",
            ]
            # Mapear campos
            if want_title: row.append(info.get("title",""))
            if want_h1: row.append(info.get("h1",""))
            if want_ogt: row.append(info.get("og_title",""))
            if want_desc: row.append(info.get("meta_description",""))
            if want_author: row.append(info.get("author",""))
            if want_updated: row.append(info.get("updated",""))
            if want_first_p: row.append(info.get("first_paragraph",""))
            if want_h2: row.append(info.get("h2_list",""))
            if want_h3: row.append(info.get("h3_list",""))
            if want_bolds:
                row.append(info.get("bolds_count",""))
                row.append(info.get("bolds_list",""))
            if want_links:
                row.append(info.get("links_count",""))
                row.append(info.get("links_anchors",""))
            if want_tags: row.append(info.get("tags",""))
            if want_rel_box:
                row.append(info.get("rel_links_count",""))
                row.append(info.get("rel_anchors",""))
                row.append(info.get("rel_urls",""))
            row.append(info.get("error",""))
            data_out.append(row)

        df_final = pd.DataFrame(data_out, columns=base_cols + extra_cols)
        st.success(f"Scraping listo. Filas: {len(df_final):,}")
        st.dataframe(df_final.head(30), use_container_width=True)

        # Crear y publicar en Sheets
        site_domain = _domain_from_site(site_url)
        title = f"Contenido (ligero) · {site_domain} · {start_date} → {end_date}"
        try:
            sid = _create_or_move_sheet(gs_client, drive_service, title, st.session_state.get("dest_folder_id"))
            sh = gs_client.open_by_key(sid)
            ws = sh.sheet1
            ws.clear()
            payload = [df_final.columns.tolist()] + df_final.fillna("").values.tolist()
            ws.update("A1", payload)
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
                analysis_kind="Contenido (ligero)",
                sheet_id=sid, sheet_name=sheet_name, sheet_url=sheet_url,
                gsc_account=st.session_state.get("src_account_label") or "",
                notes=f"n_urls={len(df_final)}; from={start_date}; to={end_date}; origin={origen}"
            )
            st.session_state["last_file_id"] = sid
            st.session_state["last_file_kind"] = "content-lite"
        except Exception as e:
            st.error(f"No pude publicar en Sheets: {e}")

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