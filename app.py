# app.py
from __future__ import annotations

# --- Permisos OAuth en localhost + tolerancia de scope
import os
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

from datetime import date, timedelta
from types import SimpleNamespace
import requests
import pandas as pd
import streamlit as st
from google.oauth2.credentials import Credentials

# ============== Config base ==============
st.set_page_config(layout="wide", page_title="Análisis SEO", page_icon="📊")

# ====== UI / Branding ======
from modules.ui import (
    apply_page_style,
    render_brand_header_once,
    enable_brand_auto_align,
    get_user,
    sidebar_user_info,
    login_screen,  # ya no se usa como gate principal, pero lo dejamos disponible
)

HEADER_COLOR = "#5c417c"
HEADER_HEIGHT = 64
LOGO_URL = "https://nomadic.agency/wp-content/uploads/2021/03/logo-blanco.png"

# Estilo general + header nativo
apply_page_style(
    header_bg=HEADER_COLOR,
    header_height_px=HEADER_HEIGHT,
    page_bg="#ffffff",
    use_gradient=False,
    band_height_px=110,
)

# Logo anclado
render_brand_header_once(
    LOGO_URL,
    height_px=27,
    pinned=True,
    nudge_px=-42,
    x_align="left",
    x_offset_px=40,
    z_index=3000,
    container_max_px=1200,
)
enable_brand_auto_align()

# ====== Estilos globales ======
st.markdown("""
<style>
.stButton > button, .stDownloadButton > button {
  background: #8e7cc3 !important; border-color: #8e7cc3 !important;
  color: #fff !important; border-radius: 8px !important;
}
.stButton > button:hover, .stDownloadButton > button:hover { filter: brightness(0.93); }
.success-inline {
  background:#e6f4ea; border:1px solid #a5d6a7; color:#1e4620;
  padding:10px 14px; border-radius:8px; display:flex; align-items:center; gap:.5rem; flex-wrap:wrap;
}
.success-inline a { color:#0b8043; text-decoration:underline; font-weight:600; }
.success-inline strong { margin-left:.25rem; }
header[data-testid="stHeader"] { z-index:1500 !important; }
</style>
""", unsafe_allow_html=True)

st.title("Analizador SEO 🚀")

# ====== Utils / paquete externo ======
from modules.utils import debug_log, ensure_external_package, token_store
_ext = ensure_external_package()

run_core_update = getattr(_ext, "run_core_update", None) if _ext else None
run_evergreen = getattr(_ext, "run_evergreen", None) if _ext else None
run_traffic_audit = getattr(_ext, "run_traffic_audit", None) if _ext else None

if run_core_update is None or run_evergreen is None:
    try:
        from modules.analysis import run_core_update as _rcu, run_evergreen as _rev  # type: ignore
        run_core_update = run_core_update or _rcu
        run_evergreen   = run_evergreen   or _rev
    except Exception:
        pass

if run_traffic_audit is None:
    try:
        from modules.analysis import run_traffic_audit as _rta  # type: ignore
        run_traffic_audit = _rta
    except Exception:
        pass

USING_EXT = bool(_ext)

# ====== OAuth / Clientes ======
from modules.auth import build_flow, pick_destination_oauth, pick_source_oauth
from modules.drive import (
    ensure_drive_clients,
    get_google_identity,
    pick_destination,
    share_controls,
)
from modules.gsc import ensure_sc_client

# ====== IA (Nomadic Bot 🤖 / Gemini) ======
from modules.ai import is_gemini_configured, summarize_sheet_auto, render_summary_box

# ------------------------------------------------------------
# Helpers de query params
# ------------------------------------------------------------
def _get_qp() -> dict:
    try:
        return dict(st.query_params)
    except Exception:
        return st.experimental_get_query_params()

def _clear_qp():
    try:
        st.query_params.clear()
    except Exception:
        st.experimental_set_query_params()

# ------------------------------------------------------------
# PASO 0: Login con Google (OIDC) para obtener identidad (email/nombre/foto)
# (usa credenciales WEB de [auth] si existen; si no, fallback manual con "installed")
# ------------------------------------------------------------
def step0_google_identity():
    """
    Inicia sesión con Google (scopes: openid email profile) para obtener identidad.
    - Si en st.secrets['auth'] hay client_id/client_secret/redirect_uri => usa flujo WEB con redirección automática.
    - Si faltan, usa flujo INSTALLED (localhost) y se pega la URL manualmente.
    """
    st.subheader("0) Iniciar sesión con Google (identidad)")

    auth_sec = st.secrets.get("auth", {}) or {}
    has_web = bool(auth_sec.get("client_id") and auth_sec.get("client_secret") and auth_sec.get("redirect_uri"))
    redirect_uri = auth_sec.get("redirect_uri")

    if "oauth_oidc" not in st.session_state:
        if has_web:
            # === Modo WEB (sin copy/paste) ===
            client_secrets = {
                "web": {
                    "client_id": auth_sec["client_id"],
                    "client_secret": auth_sec["client_secret"],
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                    "redirect_uris": [redirect_uri],
                }
            }
            from google_auth_oauthlib.flow import Flow
            flow = Flow.from_client_config(client_secrets, scopes=["openid", "email", "profile"])
            flow.redirect_uri = redirect_uri
            auth_url, state = flow.authorization_url(
                prompt="select_account",
                access_type="online",
                include_granted_scopes="true",  # string
            )
            st.session_state["oauth_oidc"] = {
                "flow": flow,
                "auth_url": auth_url,
                "state": state,
                "use_redirect": True,
                "redirect_uri": redirect_uri,
                "mode": "web",
            }
        else:
            # === Fallback INSTALLED (copy/paste) ===
            acct_for_dest = st.secrets.get("oauth_app_key", "ACCESO")
            flow = build_flow(acct_for_dest, ["openid", "email", "profile"])
            # redirect http://localhost ya viene de build_flow()
            auth_url, state = flow.authorization_url(
                prompt="select_account",
                access_type="online",
                include_granted_scopes="true",
            )
            st.session_state["oauth_oidc"] = {
                "flow": flow,
                "auth_url": auth_url,
                "state": state,
                "use_redirect": False,
                "redirect_uri": "http://localhost",
                "mode": "installed",
            }
    else:
        # Sincronizar cambios si modificaste secrets en caliente
        oo = st.session_state["oauth_oidc"]
        if has_web and oo.get("mode") != "web":
            st.session_state.pop("oauth_oidc", None)
            return step0_google_identity()
        if (not has_web) and oo.get("mode") != "installed":
            st.session_state.pop("oauth_oidc", None)
            return step0_google_identity()

    oo = st.session_state["oauth_oidc"]

    # Si venimos redirigidos desde Google (solo modo web)
    qp = _get_qp()
    code = qp.get("code", [None])[0] if isinstance(qp.get("code"), list) else qp.get("code")
    state_in = qp.get("state", [None])[0] if isinstance(qp.get("state"), list) else qp.get("state")

    if oo.get("use_redirect") and code and state_in:
        expected_state = oo.get("state")
        if state_in != expected_state:
            st.error("CSRF Warning: el 'state' devuelto no coincide con el generado.")
            st.stop()

        # Reconstruir la URL EXACTA de retorno
        from urllib.parse import urlencode
        current_url = f"{oo['redirect_uri']}?{urlencode({k: v[0] if isinstance(v, list) else v for k, v in qp.items()}, doseq=True)}"

        try:
            flow = oo["flow"]
            flow.fetch_token(authorization_response=current_url)
            creds = flow.credentials
            # Userinfo
            resp = requests.get(
                "https://openidconnect.googleapis.com/v1/userinfo",
                headers={"Authorization": f"Bearer {creds.token}"},
                timeout=10,
            )
            info = resp.json() if resp.status_code == 200 else {}
            ident = {
                "name": info.get("name") or info.get("email") or "Invitado",
                "email": info.get("email") or "—",
                "picture": info.get("picture"),
            }
            st.session_state["_google_identity"] = ident
            _clear_qp()
            st.success(f"Identidad verificada: {ident['email']}")
            return ident
        except Exception as e:
            st.error(f"No se pudo verificar identidad: {e}")
            st.stop()

    # UI inicial según modo
    if oo.get("use_redirect"):
        st.markdown(
            f'<a href="{oo["auth_url"]}" target="_self"><button type="button">Continuar con Google</button></a>',
            unsafe_allow_html=True
        )
        st.caption("Serás redirigido automáticamente de vuelta a esta app luego de autorizar.")
    else:
        st.info("Modo manual activo (no hay credenciales WEB en [auth]). Podés copiar/pegar la URL, o configurar client_id/client_secret/redirect_uri para modo automático.")
        st.markdown(f"🔗 **Paso A (identidad):** [Iniciar sesión con Google]({oo['auth_url']})")
        with st.expander("Ver/copiar URL de autorización (identidad)"):
            st.code(oo["auth_url"])

        url = st.text_input(
            "🔑 Paso B (identidad): pegá la URL completa (http://localhost/?code=...&state=...)",
            key="auth_response_url_oidc",
            placeholder="http://localhost/?code=...&state=...",
        )
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Verificar identidad", type="primary", key="btn_oidc_connect"):
                if not url.strip():
                    st.error("Pegá la URL completa de redirección (incluye code y state).")
                    st.stop()

                from urllib.parse import urlsplit, parse_qs
                try:
                    qs = parse_qs(urlsplit(url.strip()).query)
                    returned_state = (qs.get("state") or [""])[0]
                except Exception:
                    returned_state = ""
                expected_state = oo.get("state")
                if not returned_state or returned_state != expected_state:
                    st.error("CSRF Warning: el 'state' devuelto no coincide con el generado.")
                    st.stop()

                try:
                    flow = oo["flow"]
                    flow.fetch_token(authorization_response=url.strip())
                    creds = flow.credentials
                    resp = requests.get(
                        "https://openidconnect.googleapis.com/v1/userinfo",
                        headers={"Authorization": f"Bearer {creds.token}"},
                        timeout=10,
                    )
                    info = resp.json() if resp.status_code == 200 else {}
                    ident = {
                        "name": info.get("name") or info.get("email") or "Invitado",
                        "email": info.get("email") or "—",
                        "picture": info.get("picture"),
                    }
                    st.session_state["_google_identity"] = ident
                    st.success(f"Identidad verificada: {ident['email']}")
                    return ident
                except Exception as e:
                    st.error(f"No se pudo verificar identidad: {e}")
                    st.stop()
        with c2:
            if st.button("Reiniciar Paso 0", key="btn_reset_oidc"):
                st.session_state.pop("oauth_oidc", None)
                st.session_state.pop("_google_identity", None)
                st.rerun()

    return st.session_state.get("_google_identity")

# ------------------------------------------------------------
# Pantalla de LOGOUT: revoca tokens, borra cachés y limpia sesión
# ------------------------------------------------------------
def _revoke_google_token(token: str | None) -> None:
    if not token:
        return
    try:
        requests.post(
            "https://oauth2.googleapis.com/revoke",
            params={"token": token},
            headers={"content-type": "application/x-www-form-urlencoded"},
            timeout=10,
        )
    except Exception:
        pass  # no hacemos ruido si la revocación falla

def logout_screen():
    st.header("Cerrar sesión")
    ident = st.session_state.get("_google_identity") or {}
    current_email = ident.get("email") or "—"
    st.write(f"Usuario actual: **{current_email}**")

    revoke = st.checkbox("Revocar permisos de Google (Drive/Sheets y Search Console)", value=True)
    wipe_pkg = st.checkbox("Borrar caché del paquete externo (.ext_pkgs/)", value=False)

    col1, col2 = st.columns([1,1])
    with col1:
        if st.button("🔒 Cerrar sesión y limpiar", type="primary"):
            # Revocar tokens (si se solicita)
            if revoke:
                for key in ("creds_dest", "creds_src"):
                    data = st.session_state.get(key)
                    if isinstance(data, dict):
                        _revoke_google_token(data.get("token") or data.get("refresh_token"))

            # Borrar cachés
            try: st.cache_data.clear()
            except Exception: pass
            try: st.cache_resource.clear()
            except Exception: pass

            # Borrar paquete externo (opcional)
            if wipe_pkg:
                import shutil
                shutil.rmtree(".ext_pkgs", ignore_errors=True)

            # Limpiar session_state
            for k in [
                "_auth_bypass", "_google_identity",
                "oauth_oidc", "oauth_dest", "oauth_src",
                "creds_dest", "creds_src",
                "step1_done", "step2_done", "step3_done",
                "dest_folder_id", "src_account_label",
                "site_url_choice", "last_file_id",
                "DEBUG",
            ]:
                st.session_state.pop(k, None)

            # Limpiar token_store
            try:
                token_store.clear("creds_dest")
                token_store.clear("creds_src")
            except Exception:
                pass

            # Intentar cerrar sesión de Streamlit (si aplica)
            try:
                if hasattr(st, "logout"):
                    st.logout()
            except Exception:
                pass

            st.success("Sesión cerrada y caché limpiada.")
            st.markdown("➡️ Volver a la app: [Inicio](?)")
            st.stop()

    with col2:
        if st.button("Cancelar"):
            _clear_qp()
            st.rerun()

# ====== Pequeñas utilidades UI (parámetros y selección) ======
def pick_site(sc_service):
    st.subheader("4) Elegí el sitio a trabajar (Search Console)")
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


def pick_analysis(include_auditoria: bool):
    st.subheader("5) Elegí el tipo de análisis")
    opciones = [
        "1. Análisis de entidades (🚧 próximamente)",
        "2. Análisis de tráfico general (🚧 próximamente)",
        "3. Análisis de secciones (🚧 próximamente)",
        "4. Análisis de impacto de Core Update ✅",
        "5. Análisis de tráfico evergreen ✅",
    ]
    if include_auditoria:
        opciones.append("6. Auditoría de tráfico ✅")

    key = st.radio("Tipos disponibles:", opciones, index=3, key="analysis_choice")
    if key.startswith("4."):
        return "4"
    if key.startswith("5."):
        return "5"
    if key.startswith("6."):
        return "6"
    return "0"


LAG_DAYS_DEFAULT = 3

def params_for_core_update():
    st.markdown("#### Parámetros (Core Update)")
    lag_days = st.number_input("Lag de datos (para evitar días incompletos)", 0, 7, LAG_DAYS_DEFAULT, key="lag_core")
    fecha_inicio = st.date_input("¿Cuándo inició el Core Update? (YYYY-MM-DD)", key="core_ini")
    termino = st.radio("¿El Core Update ya terminó?", ["sí", "no"], horizontal=True, key="core_end")
    fecha_fin = None
    if termino == "sí":
        fecha_fin = st.date_input("¿Cuándo finalizó el Core Update? (YYYY-MM-DD)", key="core_fin")
    tipo = st.selectbox("Datos a analizar", ["Search", "Discover", "Ambos"], index=2, key="tipo_core")
    pais_choice = st.selectbox(
        "¿Filtrar por país? (ISO-3)",
        ["Todos", "ARG", "MEX", "ESP", "USA", "COL", "PER", "CHL", "URY"],
        index=0,
        key="pais_core",
    )
    pais = None if pais_choice == "Todos" else pais_choice
    seccion = st.text_input("¿Limitar a una sección? (path, ej: /vida/)", value="", key="sec_core") or None
    return lag_days, fecha_inicio, termino, fecha_fin, tipo, pais, seccion


def params_for_evergreen():
    st.markdown("#### Parámetros (Evergreen)")
    st.caption("Se usa el período más amplio posible de **meses completos** (hasta 16) en Search.")
    lag_days = st.number_input("Lag de datos (para evitar días incompletos)", 0, 7, LAG_DAYS_DEFAULT, key="lag_ev")
    pais_choice = st.selectbox(
        "¿Filtrar por país? (ISO-3)",
        ["Todos", "ARG", "MEX", "ESP", "USA", "COL", "PER", "CHL", "URY"],
        index=0,
        key="pais_ev",
    )
    pais = None if pais_choice == "Todos" else pais_choice
    seccion = st.text_input("¿Limitar a una sección? (path, ej: /vida/)", value="", key="sec_ev") or None

    # Ventana de 16 meses completos
    hoy_util = date.today() - timedelta(days=lag_days)
    end_month_first_day = (pd.Timestamp(hoy_util.replace(day=1)) - pd.offsets.MonthBegin(1))
    end_month_last_day = (end_month_first_day + pd.offsets.MonthEnd(0))
    start_month_first_day = (end_month_first_day - pd.DateOffset(months=15))
    start_date = start_month_first_day.date()
    end_date = end_month_last_day.date()
    st.info(f"Ventana mensual: {start_date} → {end_date}")

    incluir_diario = st.checkbox("Incluir análisis diario por URL (lento)", value=False, key="daily_ev")
    return lag_days, pais, seccion, incluir_diario, start_date, end_date


def params_for_auditoria():
    st.markdown("#### Parámetros (Auditoría de tráfico)")
    st.caption(
        "Un **período** es la ventana que se usa para auditar y comparar. "
        "Podés elegir **Semanal (7 días)**, **Quincenal (15 días)**, **Mensual (mes calendario)** "
        "o un **Personalizado (N días)**. Se comparan la ventana actual + N previas."
    )
    modo = st.selectbox("Modo de período", ["Semanal", "Quincenal", "Mensual", "Personalizado"], index=0, key="aud_modo")
    custom_days = None
    if modo == "Personalizado":
        custom_days = st.number_input("Días del período personalizado", 2, 90, 7, key="aud_custom_days")

    tipo = st.selectbox("Origen", ["Search", "Discover", "Search y Discover"], index=2, key="aud_tipo")
    seccion = st.text_input("Sección (path, ej: /vida/). Vacío = todo el sitio", value="", key="aud_sec") or None

    alcance = st.selectbox("Ámbito", ["Global", "País"], index=0, key="aud_ambito")
    country = None
    if alcance == "País":
        country = st.selectbox("País (ISO-3)", ["ARG","MEX","ESP","USA","COL","PER","CHL","URY"], index=0, key="aud_pais")

    periods_back = st.number_input("¿Cuántos periodos previos querés comparar?", 1, 12, 4, key="aud_prev")
    st.caption("Ej.: Semanal = 1 semana actual + N semanas previas. Mensual = 1 mes actual + N meses previos, etc.")
    lag_days = st.number_input("Lag de datos (para evitar días incompletos)", 0, 7, LAG_DAYS_DEFAULT, key="aud_lag")

    return (modo, tipo, seccion, alcance, country, lag_days, custom_days, periods_back)


# ============== App ==============

# Detectar pantalla de logout por query param
_view = _get_qp().get("view")
if isinstance(_view, list):
    _view = _view[0] if _view else None
if _view == "logout":
    logout_screen()
    st.stop()

# Preferir Paso 0 (OIDC) si así se indica en secrets
prefer_oidc = bool(st.secrets.get("auth", {}).get("prefer_oidc", True))

# 1) Identidad Google ya guardada?
ident = st.session_state.get("_google_identity")

# 2) Usuario de Streamlit (si el sharing es “Only specific people”, puede venir ya logueado)
user = get_user()

# 3) Si había bypass activo y preferimos OIDC, lo limpiamos para mostrar Paso 0
if prefer_oidc and st.session_state.get("_auth_bypass"):
    st.session_state.pop("_auth_bypass", None)
    user = None

# 4) Mostrar SIEMPRE Paso 0 si prefer_oidc y aún no hay identidad
if prefer_oidc and not ident:
    ident = step0_google_identity()
    if not ident:
        st.stop()

# 5) Si no hay user de Streamlit, creamos uno sintético con la identidad OIDC
if not user:
    if ident:
        user = SimpleNamespace(
            is_logged_in=True,
            name=(ident.get("name") or "Invitado"),
            email=(ident.get("email") or "—"),
            picture=ident.get("picture"),
        )
    else:
        login_screen()
        st.stop()

# Sidebar → Mantenimiento (incluye acceso a logout)
def maintenance_extra_ui():
    if USING_EXT:
        st.caption("🧩 Usando análisis del paquete externo (repo privado).")
    else:
        st.caption("🧩 Usando análisis embebidos en este repo.")
    st.checkbox("🔧 Modo debug (Drive/GSC)", key="DEBUG")
    st.markdown("[🔒 Ir a pantalla de Logout](?view=logout)")

sidebar_user_info(user, maintenance_extra=maintenance_extra_ui)

# Estados de pasos
st.session_state.setdefault("step1_done", False)
st.session_state.setdefault("step2_done", False)
st.session_state.setdefault("step3_done", False)

# === Acciones de query ===
_qp = _get_qp()
_action = _qp.get("action")
if isinstance(_action, list):
    _action = _action[0] if _action else None

if _action == "change_personal":
    for k in ("creds_dest", "oauth_dest", "step1_done"):
        st.session_state.pop(k, None)
    st.session_state["step2_done"] = False
    st.session_state.pop("dest_folder_id", None)
    _clear_qp(); st.rerun()

elif _action == "change_folder":
    st.session_state["step2_done"] = False
    _clear_qp(); st.rerun()

elif _action == "change_src":
    for k in ("creds_src", "oauth_src", "step3_done"):
        st.session_state.pop(k, None)
    _clear_qp(); st.rerun()


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

# Si ya está completo, clientes + resumen
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
                <a href="?action=change_personal">(Cambiar mail personal)</a>
            </div>
            ''',
            unsafe_allow_html=True
        )
    except Exception as e:
        st.error(f"No pude inicializar Drive/Sheets con la cuenta PERSONAL: {e}")
        st.stop()

# --- PASO 2: Carpeta destino (opcional) ---
if not st.session_state["step2_done"]:
    st.subheader("2) Destino de la copia (opcional)")
    dest_folder_id = pick_destination(drive_service, _me, show_header=False)
    st.caption("Si no elegís carpeta, se creará en **Mi unidad**.")
    if st.button("Siguiente ⏭️", key="btn_next_step2"):
        st.session_state["step2_done"] = True
        st.rerun()
else:
    chosen = st.session_state.get("dest_folder_id")
    pretty = "Mi unidad (raíz)" if not chosen else "Carpeta personalizada seleccionada"
    st.markdown(
        f'''
        <div class="success-inline">
            Destino de la copia: <strong>{pretty}</strong>
            <a href="?action=change_folder">(Cambiar carpeta)</a>
        </div>
        ''',
        unsafe_allow_html=True
    )

# --- PASO 3: Conectar Search Console (fuente de datos) ---
sc_service = None
if not st.session_state["step3_done"]:
    creds_src = pick_source_oauth()
    if not creds_src:
        st.stop()
    st.session_state["creds_src"] = {
        "token": creds_src.token,
        "refresh_token": getattr(creds_src, "refresh_token", None),
        "token_uri": creds_src.token_uri,
        "client_id": creds_src.client_id,
        "client_secret": creds_src.client_secret,
        "scopes": creds_src.scopes,
    }
    src_account = (st.session_state.get("oauth_src") or {}).get("account") or "ACCESO"
    st.session_state["src_account_label"] = src_account
    st.session_state["step3_done"] = True
    st.rerun()
else:
    try:
        creds_src = Credentials(**st.session_state["creds_src"])
        sc_service = ensure_sc_client(creds_src)
        src_label = st.session_state.get("src_account_label") or "ACCESO"
        st.markdown(
            f'''
            <div class="success-inline">
                Cuenta de acceso (Search Console): <strong>{src_label}</strong>
                <a href="?action=change_src">(Cambiar cuenta de acceso)</a>
            </div>
            ''',
            unsafe_allow_html=True
        )
    except Exception as e:
        st.error(f"No pude inicializar el cliente de Search Console: {e}")
        st.stop()

# --- PASO 4: sitio + PASO 5: análisis ---
site_url = pick_site(sc_service)
include_auditoria = run_traffic_audit is not None
analisis = pick_analysis(include_auditoria)

# --- Ejecutar ---
def run_with_indicator(titulo: str, fn, *args, **kwargs):
    mensaje = f"⏳ {titulo}… Esto puede tardar varios minutos."
    if hasattr(st, "status"):
        with st.status(mensaje, expanded=True) as status:
            res = fn(*args, **kwargs)
            status.update(label="✅ Informe generado", state="complete")
            return res
    else:
        with st.spinner(mensaje):
            return fn(*args, **kwargs)

if analisis == "4":
    if run_core_update is None:
        st.warning("Este despliegue no incluye run_core_update.")
    else:
        params = params_for_core_update()
        if st.button("🚀 Ejecutar análisis de Core Update", type="primary"):
            sid = run_with_indicator(
                "Procesando Core Update",
                run_core_update, sc_service, drive_service, gs_client, site_url, params,
                st.session_state.get("dest_folder_id")
            )
            st.success("¡Listo! Tu documento está creado.")
            st.markdown(f"➡️ **Abrir Google Sheets**: https://docs.google.com/spreadsheets/d/{sid}")
            share_controls(drive_service, sid, default_email=_me.get("emailAddress") if _me else None)
            st.session_state["last_file_id"] = sid

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
            st.success("¡Listo! Tu documento está creado.")
            st.markdown(f"➡️ **Abrir Google Sheets**: https://docs.google.com/spreadsheets/d/{sid}")
            share_controls(drive_service, sid, default_email=_me.get("emailAddress") if _me else None)
            st.session_state["last_file_id"] = sid

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
            st.success("¡Listo! Tu documento está creado.")
            st.markdown(f"➡️ **Abrir Google Sheets**: https://docs.google.com/spreadsheets/d/{sid}")
            share_controls(drive_service, sid, default_email=_me.get("emailAddress") if _me else None)
            st.session_state["last_file_id"] = sid

            # ===== Resumen con IA (Nomadic Bot 🤖) =====
            st.divider()
            use_ai = st.toggle(
                "Generar resumen con IA (Nomadic Bot 🤖)",
                value=True,
                help="Usa Gemini para leer el Google Sheet y crear un resumen breve y accionable."
            )
            if use_ai:
                if is_gemini_configured():
                    with st.spinner("🤖 Nomadic Bot está leyendo tu informe y generando un resumen…"):
                        md = summarize_sheet_auto(gs_client, sid, kind="audit")
                    render_summary_box(md)
                else:
                    st.info("🔐 Configurá tu API key de Gemini en Secrets (`GEMINI_API_KEY` o `[gemini].api_key`).")

else:
    st.info("Las opciones 1, 2 y 3 aún no están disponibles en esta versión.")

# Debug opcional para verificar si la API key de Gemini está disponible
st.write(
    "¿Gemini listo?",
    "GEMINI_API_KEY" in st.secrets or ("gemini" in st.secrets and "api_key" in st.secrets["gemini"])
)