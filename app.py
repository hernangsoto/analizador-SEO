from __future__ import annotations

# --- Permisos OAuth en localhost + tolerancia de scope
import os
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

from datetime import date, timedelta, datetime
from types import SimpleNamespace
import time
import requests
import pandas as pd
import streamlit as st
from google.oauth2.credentials import Credentials

# Extras para manejo de errores detallados
import json
from gspread.exceptions import APIError as GspreadAPIError
try:
    from googleapiclient.errors import HttpError
except Exception:
    HttpError = Exception

# ============== Config base ==============
st.set_page_config(layout="wide", page_title="Análisis SEO", page_icon="📊")

# ====== UI / Branding ======
from modules.ui import (
    apply_page_style,
    # render_brand_header_once,
    # enable_brand_auto_align,
    get_user,
    sidebar_user_info,
    login_screen,
)

HEADER_COLOR = "#5c417c"
HEADER_HEIGHT = 64
LOGO_URL = "https://nomadic.agency/wp-content/uploads/2021/03/logo-blanco.png"

# URL base del app (para enlaces internos que deben ir SIEMPRE a la home)
APP_HOME = st.secrets.get("app_home_url", "https://hernangsoto.streamlit.app")

# === Modelo de Gemini (default + override por secrets/env) ===
def _resolve_gemini_model() -> str:
    return (
        os.getenv("GEMINI_MODEL")
        or st.secrets.get("GEMINI_MODEL")
        or st.secrets.get("gemini", {}).get("model")
        or "gemini-2.5-flash"
    )

# Deja el modelo elegido disponible para cualquier módulo que lo lea desde el entorno
os.environ["GEMINI_MODEL"] = _resolve_gemini_model()

# Estilo general + header nativo
apply_page_style(
    header_bg=HEADER_COLOR,
    header_height_px=HEADER_HEIGHT,
    page_bg="#ffffff",
    use_gradient=False,
    band_height_px=110,
)

# --- Logo Nomadic siempre visible (CSS sobre el header) ---
def pin_nomadic_logo_css(logo_url: str):
    st.markdown(
        f"""
        <style>
        header[data-testid="stHeader"] {{
            position: sticky !important;
            top: 0;
            z-index: 1500 !important;
        }}
        header[data-testid="stHeader"]::before {{
            content: "";
            position: fixed;
            left: 40px;
            top: 14px;
            width: 140px;
            height: 27px;
            background-image: url('{logo_url}');
            background-repeat: no-repeat;
            background-size: contain;
            pointer-events: none;
            z-index: 4000;
        }}
        @media (max-width: 600px) {{
            header[data-testid="stHeader"]::before {{
                left: 16px;
                top: 12px;
                width: 120px;
                height: 24px;
            }}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )

pin_nomadic_logo_css(LOGO_URL)

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

/* Mantener header por encima del contenido */
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

# -- NUEVO runner: Análisis de Nombres (KG + Wikipedia)
run_names_analysis = getattr(_ext, "run_names_analysis", None) if _ext else None

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

# Intento de import para Nombres desde repo privado y fallback local
if run_names_analysis is None:
    try:
        from seo_analisis_ext.analysis_names import run_names_analysis as _rna  # type: ignore
        run_names_analysis = _rna
    except Exception:
        try:
            from modules.analysis_names import run_names_analysis as _rna  # type: ignore
            run_names_analysis = _rna
        except Exception:
            run_names_analysis = None

USING_EXT = bool(_ext)

# ====== OAuth / Clientes ======
from modules.auth import (
    build_flow,
    pick_destination_oauth,
    pick_source_oauth,
    SCOPES_DRIVE,            # <-- Drive/Sheets
)
from modules.drive import (
    ensure_drive_clients,
    get_google_identity,
    pick_destination,
    share_controls,
)
from modules.gsc import ensure_sc_client

# ---- Scope de Search Console para Paso 0 (cuenta principal)
SCOPES_GSC = ["https://www.googleapis.com/auth/webmasters.readonly"]

# ====== IA (Nomadic Bot 🤖 / Gemini) ======
from modules.ai import is_gemini_configured, summarize_sheet_auto, render_summary_box
import importlib.util, pathlib
import re

_SUMMARIZE_WITH_PROMPT = None
_PROMPTS = None
_AI_SRC = "none"
_AI_IMPORT_ERR = None

def _load_prompts():
    """Carga PROMPTS y summarize_sheet_with_prompt del repo privado; si falla, usa fallback local."""
    global _SUMMARIZE_WITH_PROMPT, _PROMPTS, _AI_SRC, _AI_IMPORT_ERR
    _SUMMARIZE_WITH_PROMPT = None
    _PROMPTS = None
    _AI_SRC = "none"
    _AI_IMPORT_ERR = None
    e_ext = e_file = e_loc = None

    # 1) Import estándar del paquete externo
    try:
        from seo_analisis_ext.ai_summaries import summarize_sheet_with_prompt as _s, PROMPTS as _p  # type: ignore
        _SUMMARIZE_WITH_PROMPT, _PROMPTS, _AI_SRC = _s, _p, "external"
        return
    except Exception as ex:
        e_ext = ex

    # 2) Carga por archivo junto al paquete externo
    try:
        if _ext:
            base = pathlib.Path(_ext.__file__).parent
            f = base / "ai_summaries.py"
            if f.exists():
                spec = importlib.util.spec_from_file_location("seo_analisis_ext_ai_summaries", f)
                mod = importlib.util.module_from_spec(spec)  # type: ignore
                assert spec and spec.loader
                spec.loader.exec_module(mod)  # type: ignore
                _SUMMARIZE_WITH_PROMPT = getattr(mod, "summarize_sheet_with_prompt", None)
                _PROMPTS = getattr(mod, "PROMPTS", None)
                if _SUMMARIZE_WITH_PROMPT and _PROMPTS:
                    _AI_SRC = f"external:file:{f.name}"
                    return
    except Exception as ef:
        e_file = ef

    # 3) Fallback local
    try:
        from modules.ai_summaries import summarize_sheet_with_prompt as _s, PROMPTS as _p  # type: ignore
        _SUMMARIZE_WITH_PROMPT, _PROMPTS, _AI_SRC = _s, _p, "local"
        return
    except Exception as el:
        e_loc = el
        _AI_IMPORT_ERR = f"external={repr(e_ext)} | file={repr(e_file)} | local={repr(e_loc)}"

_load_prompts()

def _gemini_healthcheck():
    ok = True
    msgs = []
    try:
        has_key = bool(os.environ.get("GEMINI_API_KEY")) or (
            ("GEMINI_API_KEY" in st.secrets)
            or ("gemini" in st.secrets and "api_key" in st.secrets["gemini"])
        )
    except Exception:
        has_key = False
    msgs.append(f"API key presente: {has_key}")

    try:
        import google.generativeai as genai  # noqa
        msgs.append(f"google-generativeai importado: True (v={getattr(genai, '__version__', 'desconocida')})")
        if has_key:
            key = os.environ.get("GEMINI_API_KEY") or \
                  (st.secrets.get("GEMINI_API_KEY")) or \
                  (st.secrets.get("gemini", {}).get("api_key"))
            genai.configure(api_key=key)
            model_name = os.environ.get("GEMINI_MODEL") or _resolve_gemini_model()
            _ = genai.GenerativeModel(model_name)
            msgs.append(f"Modelo instanciado: {model_name}")
        else:
            ok = False
            msgs.append("Falta API key: define GEMINI_API_KEY o [gemini].api_key en secrets.")
    except Exception as e:
        ok = False
        msgs.append(f"Error al importar/configurar Gemini: {repr(e)}")

    return ok, msgs

def _render_prompt_probe(kind: str, force_key: str | None = None):
    key = force_key or kind
    st.markdown("### 🔍 Test de prompt")
    st.caption(f"Tipo solicitado: **{kind}**  •  Clave buscada: **{key}**")

    if _PROMPTS is None:
        st.error(f"No pude cargar PROMPTS (fuente: {_AI_SRC}). Usaría fallback automático.")
        if _AI_IMPORT_ERR:
            with st.expander("Ver detalle del error de import"):
                st.code(_AI_IMPORT_ERR)
        return

    st.caption(f"Fuente de prompts: **{_AI_SRC}**")
    with st.expander("Claves disponibles en PROMPTS"):
        try:
            st.write(", ".join(sorted(list(_PROMPTS.keys()))))
        except Exception:
            try:
                st.write(list(_PROMPTS.keys()))
            except Exception:
                st.write("(No se pudieron listar las claves)")

    if key not in _PROMPTS:
        st.error(f"No hay PROMPTS['{key}']. Se usaría fallback automático.")
        return

    pc = _PROMPTS[key]
    st.success(f"✅ Encontrado PROMPTS['{key}']. Este es el prompt que se usará.")
    st.markdown(f"**title:** {getattr(pc, 'title', '(sin título)')}")
    st.markdown("**system:**")
    st.code(getattr(pc, 'system', ''), language="md")
    bh = getattr(pc, 'bullets_hint', None)
    if bh:
        st.markdown("**bullets_hint:**")
        st.code(bh, language="md")

if st.session_state.get("DEBUG"):
    with st.expander("🧪 Diagnóstico rápido de prompts (opcional)", expanded=False):
        try:
            spec = importlib.util.find_spec("seo_analisis_ext.ai_summaries")
            st.write("Ubicación de seo_analisis_ext.ai_summaries:", getattr(spec, "origin", "(no encontrada)"))
        except Exception:
            st.write("Ubicación de seo_analisis_ext.ai_summaries: (no disponible)")

        st.write("Fuente actual de prompts:", _AI_SRC or "none")
        if _AI_IMPORT_ERR:
            st.warning("Fallo al importar prompts. Ver detalle debajo.")
            with st.expander("Detalle del error de import"):
                st.code(_AI_IMPORT_ERR)

        if st.button("🔁 Reintentar carga de prompts"):
            _load_prompts()
            st.rerun()

# === 🔎 Panel de diagnóstico (DEBUG) ===
def _scan_repo_for_gsc_and_filters():
    import os, re
    import pandas as pd
    roots = ['modules', '.ext_pkgs', '.']
    skip_dirs = {'.git', '.venv', 'venv', '__pycache__', '.streamlit', '.pythonlibs', '.mypy_cache', '.ruff_cache', '.cache'}
    patterns = {
        r"searchanalytics\(\)\.query": "Llamada a GSC: searchanalytics().query",
        r"\bwebmasters\.\w*?searchanalytics\(\)\.query": "Llamada a GSC (cliente webmasters)",
        r"\bservice\.\w*?searchanalytics\(\)\.query": "Llamada a GSC (objeto service)",
        r"dimensionFilterGroups": "Filtro en la query (dimensionFilterGroups)",
        r"dimensionFilter": "Filtro en la query (dimensionFilter)",
        r"\brequest\s*=\s*{": "Construcción de request body",
        r"\bbody\s*=\s*{": "Construcción de request body",
        r'"dimensions"\s*:\s*\[': "Definición de dimensiones en request",
        r'"dimension"\s*:\s*"PAGE"': "Dimensión PAGE dentro de filtros",
        r"df\[['\"]page['\"]\]": "Uso de columna page en DataFrame",
        r"page\s*\.str\.(?:contains|startswith|endswith)\(": "Filtro string sobre page (postproceso)",
        r"\.query\(\s*['\"].*page.*['\"]\s*\)": "Filtro con DataFrame.query sobre page",
    }
    compiled = [(re.compile(p), label) for p, label in patterns.items()]
    results = []
    def _skip_dir(path):
        name = os.path.basename(path)
        return (name in skip_dirs) or name.startswith('.')
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not _skip_dir(os.path.join(dirpath, d))]
            for fn in filenames:
                if not fn.endswith('.py'):
                    continue
                path = os.path.join(dirpath, fn)
                try:
                    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                        for i, line in enumerate(f, 1):
                            for rx, label in compiled:
                                if rx.search(line):
                                    results.append({
                                        "file": path, "line": i, "label": label, "pattern": rx.pattern, "snippet": line.strip(),
                                    })
                except Exception:
                    pass
    st.session_state["_scan_results"] = results
    return results

def _read_context(path: str, line_no: int, around: int = 8) -> str:
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
        start = max(0, line_no - 1 - around)
        end = min(len(lines), line_no - 1 + around + 1)
        blocks = []
        for idx in range(start, end):
            prefix = ">>> " if (idx + 1) == line_no else "    "
            blocks.append(f"{prefix}{idx+1:>5}: {lines[idx].rstrip()}")
        return "\n".join(blocks)
    except Exception as e:
        return f"(No se pudo abrir {path}: {e})"

if st.session_state.get("DEBUG"):
    with st.expander("🛠️ Diagnóstico de filtros de Search Console", expanded=False):
        st.caption("Escanea el código para ubicar dónde llamas a la API de GSC y dónde aplicas filtros por URL (columna 'page').")
        if st.button("Escanear código (GSC + filtros)", key="btn_scan_gsc_files"):
            _scan_repo_for_gsc_and_filters()
        results = st.session_state.get("_scan_results", [])
        if results:
            import pandas as pd
            df = pd.DataFrame(results)[["file", "line", "label", "snippet", "pattern"]]
            st.write(f"Coincidencias encontradas: **{len(df)}**")
            st.dataframe(df, use_container_width=True, height=340)
            csv_bytes = df.to_csv(index=False).encode("utf-8")
            st.download_button("⬇️ Descargar CSV de coincidencias", data=csv_bytes, file_name="diagnostico_gsc_filtros.csv", mime="text/csv", key="dl_scan_gsc_csv")
            options = [f"{i+1}. {row.file}:{row.line} — {row.label}" for i, row in df.iterrows()]
            sel = st.selectbox("Ver contexto de una coincidencia:", options, index=0, key="sel_scan_item")
            if sel:
                idx = int(sel.split(".")[0]) - 1
                row = df.iloc[idx]
                ctx = _read_context(row["file"], int(row["line"]), around=8)
                st.code(ctx, language="python")
        else:
            st.info("Aún no hay resultados. Pulsa **Escanear código (GSC + filtros)** para empezar.")

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
# Almacén global de OAuth Flows (compartido entre pestañas/sesiones)
# ------------------------------------------------------------
@st.cache_resource
def _oauth_flow_store():
    return {}

# ------------------------------------------------------------
# PASO 0: Login con Google (OIDC + Drive/Sheets + Search Console)
# ------------------------------------------------------------
def _append_hd(auth_url: str, domain: str = "nomadic.agency") -> str:
    sep = "&" if "?" in auth_url else "?"
    return f"{auth_url}{sep}hd={domain}"

def _email_is_nomadic(email: str | None) -> bool:
    return bool(email and email.lower().endswith("@nomadic.agency"))

def _fetch_userinfo_json_with_retry(access_token: str) -> dict:
    url = "https://openidconnect.googleapis.com/v1/userinfo"
    headers = {"Authorization": f"Bearer {access_token}"}
    for attempt in range(4):
        try:
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code in (500, 502, 503, 504):
                time.sleep(1.2 * (attempt + 1))
                continue
            return r.json() if r.status_code == 200 else {}
        except requests.RequestException:
            time.sleep(1.2 * (attempt + 1))
    return {}

def step0_google_identity():
    st.subheader("¡Bienvenido! Para comenzar, inicia sesión con tu mail personal de Nomadic")
    auth_sec = st.secrets.get("auth", {}) or {}
    has_web = bool(auth_sec.get("client_id") and auth_sec.get("client_secret") and auth_sec.get("redirect_uri"))
    redirect_uri = auth_sec.get("redirect_uri")
    store = _oauth_flow_store()
    scopes_step0 = ["openid", "email", "profile"] + SCOPES_DRIVE + SCOPES_GSC

    if "oauth_oidc" not in st.session_state:
        if has_web:
            client_secrets = {"web": {
                "client_id": auth_sec["client_id"],
                "client_secret": auth_sec["client_secret"],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                "redirect_uris": [redirect_uri],
            }}
            from google_auth_oauthlib.flow import Flow
            flow = Flow.from_client_config(client_secrets, scopes=scopes_step0)
            flow.redirect_uri = redirect_uri
            auth_url, state = flow.authorization_url(
                prompt="consent select_account", access_type="offline", include_granted_scopes="true",
            )
            auth_url = _append_hd(auth_url)
            st.session_state["oauth_oidc"] = {
                "flow_state": state, "use_redirect": True, "redirect_uri": redirect_uri, "mode": "web", "auth_url": auth_url,
            }
            store[state] = {"flow": flow, "created": time.time(), "mode": "web"}
        else:
            acct_for_dest = st.secrets.get("oauth_app_key", "ACCESO")
            flow = build_flow(acct_for_dest, scopes_step0)
            auth_url, state = flow.authorization_url(
                prompt="consent select_account", access_type="offline", include_granted_scopes="true",
            )
            auth_url = _append_hd(auth_url)
            st.session_state["oauth_oidc"] = {
                "flow_state": state, "use_redirect": False, "redirect_uri": "http://localhost", "mode": "installed", "auth_url": auth_url,
            }
            store[state] = {"flow": flow, "created": time.time(), "mode": "installed"}
    else:
        oo = st.session_state["oauth_oidc"]
        if has_web and oo.get("mode") != "web":
            st.session_state.pop("oauth_oidc", None); return step0_google_identity()
        if (not has_web) and oo.get("mode") != "installed":
            st.session_state.pop("oauth_oidc", None); return step0_google_identity()

    oo = st.session_state["oauth_oidc"]
    auth_url = oo["auth_url"]
    qp = _get_qp()
    code = qp.get("code", [None])[0] if isinstance(qp.get("code"), list) else qp.get("code")
    state_in = qp.get("state", [None])[0] if isinstance(qp.get("state"), list) else qp.get("state")

    def _finalize_identity(creds, info):
        ident = {"name": info.get("name") or info.get("email") or "Invitado", "email": info.get("email") or "—", "picture": info.get("picture")}
        hd_ok = (info.get("hd") == "nomadic.agency") if info.get("hd") else False
        if not (_email_is_nomadic(ident["email"]) or hd_ok):
            st.error("Debes iniciar sesión con un correo **@nomadic.agency**.")
            st.session_state.pop("_google_identity", None)
            st.session_state.pop("creds_dest", None)
            st.session_state.pop("step1_done", None)
            _clear_qp()
            st.stop()

        st.session_state["_google_identity"] = ident
        st.session_state["creds_dest"] = {
            "token": creds.token,
            "refresh_token": getattr(creds, "refresh_token", None),
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": creds.scopes,
        }
        st.session_state["step1_done"] = True  # omite Paso 1
        _clear_qp()
        st.success(f"Identidad verificada y permisos listos: {ident['email']}")
        return ident

    # ====== PARCHE: rehidratación de state para redirección ======
    if oo.get("use_redirect") and code:
        expected_state = oo.get("flow_state")
        flow = None
        store = _oauth_flow_store()

        # 1) Intentar recuperar el Flow usando el state recibido
        if state_in and state_in in store:
            flow = store.pop(state_in)["flow"]

        # 2) Si no está en memoria, rehidratar/recrear con el state de la URL
        if not flow:
            st.info("Intentando recuperar sesión…")
            if has_web:
                from google_auth_oauthlib.flow import Flow
                client_secrets = {"web": {
                    "client_id": auth_sec["client_id"],
                    "client_secret": auth_sec["client_secret"],
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                    "redirect_uris": [redirect_uri],
                }}
                # Rehidratar con el state recibido
                flow = Flow.from_client_config(client_secrets, scopes=scopes_step0, state=state_in)
                flow.redirect_uri = redirect_uri
            else:
                # Modo installed/manual
                acct_for_dest = st.secrets.get("oauth_app_key", "ACCESO")
                flow = build_flow(acct_for_dest, scopes_step0)
                try:
                    setattr(flow, "_state", state_in)
                except Exception:
                    pass

        # 3) Aviso si el state no coincide, pero continuar con el flujo rehidratado
        if expected_state and state_in and state_in != expected_state:
            st.info("Aviso: el 'state' no coincide (posible nueva pestaña). Usando el flujo rehidratado con el state recibido…")

        try:
            # 🔧 CLAVE DEL PARCHE: usar el code directamente para evitar el chequeo estricto de state
            flow.fetch_token(code=code)
            creds = flow.credentials
            info = _fetch_userinfo_json_with_retry(creds.token)
            return _finalize_identity(creds, info)
        except Exception as e:
            st.error(f"No se pudo verificar identidad: {e}")
            st.stop()

    if oo.get("use_redirect"):
        try:
            st.link_button("Continuar con Google", auth_url)
        except Exception:
            st.markdown(
                f'<a href="{auth_url}" target="_self" rel="noopener" '
                f'style="display:inline-block;padding:.6rem 1rem;border-radius:8px;'
                f'background:#8e7cc3;color:#fff;text-decoration:none;font-weight:600;">'
                f'Continuar con Google</a>',
                unsafe_allow_html=True
            )
        st.caption("Serás redirigido a esta app automáticamente después de otorgar permisos.")
    else:
        st.info("Modo manual activo (no hay credenciales WEB en [auth]).")
        st.markdown(f"🔗 **Paso A (identidad):** [Iniciar sesión con Google]({auth_url})")
        with st.expander("Ver/copiar URL de autorización (identidad)"):
            st.code(auth_url)
        url = st.text_input(
            "🔑 Paso B (identidad): pegá la URL completa (http://localhost/?code=...&state=...)",
            key="auth_response_url_oidc",
            placeholder="http://localhost/?code=...&state=..."
        )
        c1, c2 = st.columns(2)
        with c1:
            # ====== PARCHE: rehidratación de state en modo manual ======
            if st.button("Verificar identidad", type="primary", key="btn_oidc_connect"):
                raw = (url or "").strip()
                if not raw:
                    st.error("Pegá la URL completa de redirección (incluye code y state).")
                    st.stop()
                try:
                    from urllib.parse import urlparse, parse_qs
                    state_in_manual = None
                    code_in_manual = None
                    try:
                        q = parse_qs(urlparse(raw).query)
                        state_in_manual = q.get("state", [None])[0]
                        code_in_manual = q.get("code", [None])[0]
                    except Exception:
                        pass

                    if not code_in_manual:
                        st.error("La URL no contiene el parámetro ?code=…")
                        st.stop()

                    flow_state = oo.get("flow_state")
                    store = _oauth_flow_store()
                    flow = None

                    # 1) Priorizar el Flow guardado para el state que vino en la URL
                    key_state = state_in_manual or flow_state
                    if key_state and key_state in store:
                        flow = store.pop(key_state)["flow"]

                    # 2) Si no existe, recrear y setear el state best-effort
                    if not flow:
                        acct_for_dest = st.secrets.get("oauth_app_key", "ACCESO")
                        flow = build_flow(acct_for_dest, scopes_step0)
                        try:
                            setattr(flow, "_state", key_state)
                        except Exception:
                            pass

                    # 🔧 CLAVE DEL PARCHE: usar el code directamente (evita el chequeo de state)
                    flow.fetch_token(code=code_in_manual)
                    creds = flow.credentials
                    info = _fetch_userinfo_json_with_retry(creds.token)
                    return _finalize_identity(creds, info)
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
# Pantalla de LOGOUT
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
        pass

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
            if revoke:
                for key in ("creds_dest", "creds_src"):
                    data = st.session_state.get(key)
                    if isinstance(data, dict):
                        _revoke_google_token(data.get("token") or data.get("refresh_token"))
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
    with col2:
        if st.button("Cancelar"):
            _clear_qp()
            st.rerun()

# ====== Pequeñas utilidades UI ======
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

LAG_DAYS_DEFAULT = 3

# -------- Helpers de filtros avanzados (Core Update) --------
def _parse_paths_csv(txt: str) -> list[str]:
    if not txt:
        return []
    items = [p.strip() for p in txt.split(",")]
    items = [p for p in items if p]
    return items

def _build_advanced_filters_payload(
    sec_mode: str, sec_paths: list[str],
    sub_enabled: bool, sub_mode: str | None, sub_paths: list[str] | None
) -> dict | None:
    payload: dict = {}
    if sec_mode in ("Incluir solo", "Excluir") and sec_paths:
        payload["sections"] = {"mode": "include" if sec_mode == "Incluir solo" else "exclude", "paths": sec_paths}
    if sub_enabled and sub_mode and sub_paths:
        payload["subsections"] = {"mode": "include" if sub_mode == "Incluir solo" else "exclude", "paths": sub_paths}
    return payload or None

def params_for_core_update():
    st.markdown("#### Configuración del análisis")
    lag_days = st.number_input("Lag de datos (para evitar días incompletos)", 0, 7, LAG_DAYS_DEFAULT, key="lag_core")
    presets = [
        "Core Update de junio 2025",
        "Core Update de marzo 2025",
        "Core Update de diciembre 2024",
        "Core Update de noviembre 2024",
        "Core Update de agosto 2024",
        "Personalizado",
    ]
    core_choice = st.selectbox("Core Update", presets, index=0, key="core_choice")
    custom_ini = None
    custom_fin = None
    if core_choice == "Personalizado":
        st.caption("Podés dejar sin fecha de fin si el Core Update sigue en curso.")
        custom_ini = st.date_input("Fecha de inicio (YYYY-MM-DD)", key="core_custom_ini")
        termino = st.radio("¿El Core Update ya terminó?", ["sí", "no"], horizontal=True, key="core_custom_endflag")
        if termino == "sí":
            custom_fin = st.date_input("Fecha de fin (YYYY-MM-DD)", key="core_custom_fin")
        else:
            custom_fin = None
    tipo_display = st.selectbox("Datos a analizar", ["Search", "Discover", "Search + Discover"], index=2, key="tipo_core_display")
    tipo_map = {"Search": "Search", "Discover": "Discover", "Search + Discover": "Ambos"}
    tipo = tipo_map.get(tipo_display, "Ambos")
    pais_choice = st.selectbox("¿Filtrar por país? (ISO-3)", ["Todos", "ARG", "MEX", "ESP", "USA", "COL", "PER", "CHL", "URY"], index=0, key="pais_core")
    pais = None if pais_choice == "Todos" else pais_choice
    st.markdown("##### Filtro por secciones")
    sec_mode = st.radio("¿Cómo aplicar el filtro de sección?", ["No filtrar", "Incluir solo", "Excluir"], index=0, horizontal=True, key="sec_mode_core")
    sec_list_txt = st.text_input("Secciones (separa múltiples rutas con coma, ej.: /vida/, /ciencia/)", value="", key="sec_list_core", placeholder="/vida/, /ciencia/")
    st.markdown("##### Filtro por subsecciones (opcional)")
    sub_enabled = st.checkbox("Activar filtro por subsecciones", value=False, key="subsec_en_core")
    sub_mode = None
    sub_list_txt = None
    if sub_enabled:
        sub_mode = st.radio("Modo de subsecciones", ["Incluir solo", "Excluir"], index=0, horizontal=True, key="subsec_mode_core")
        sub_list_txt = st.text_input("Subsecciones (separa múltiples rutas con coma, ej.: /vida/salud/, /vida/bienestar/)", value="", key="subsec_list_core", placeholder="/vida/salud/, /vida/bienestar/")
    sec_paths = _parse_paths_csv(sec_list_txt)
    sub_paths = _parse_paths_csv(sub_list_txt) if sub_list_txt is not None else None
    adv_payload = _build_advanced_filters_payload(sec_mode, sec_paths, sub_enabled, sub_mode, sub_paths)
    st.session_state["core_filters_payload"] = adv_payload
    seccion_legacy = None
    if adv_payload and "sections" in adv_payload:
        if adv_payload["sections"]["mode"] == "include" and len(adv_payload["sections"]["paths"]) == 1 and "subsections" not in adv_payload:
            seccion_legacy = adv_payload["sections"]["paths"][0]
    if not adv_payload and sec_list_txt.strip():
        first = _parse_paths_csv(sec_list_txt)[:1]
        seccion_legacy = first[0] if first else None
    return lag_days, core_choice, custom_ini, custom_fin, tipo, pais, seccion_legacy

def params_for_evergreen():
    st.markdown("#### Parámetros (Evergreen)")
    st.caption("Se usa el período más amplio posible de **meses completos** (hasta 16) en Search.")
    lag_days = st.number_input("Lag de datos (para evitar días incompletos)", 0, 7, LAG_DAYS_DEFAULT, key="lag_ev")
    pais_choice = st.selectbox("¿Filtrar por país? (ISO-3)", ["Todos", "ARG", "MEX", "ESP", "USA", "COL", "PER", "CHL", "URY"], index=0, key="pais_ev")
    pais = None if pais_choice == "Todos" else pais_choice
    seccion = st.text_input("¿Limitar a una sección? (path, ej: /vida/)", value="", key="sec_ev") or None
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

# ======== Parámetros (Nombres KG + Wikipedia) ========
def _load_names_from_csv(uploaded_file) -> pd.DataFrame | None:
    if not uploaded_file:
        return None
    try:
        df = pd.read_csv(uploaded_file)
        return df if not df.empty else None
    except Exception:
        try:
            uploaded_file.seek(0)
        except Exception:
            pass
        st.error("No pude leer el CSV. Asegurate de que esté en UTF-8 y separado por comas.")
        return None

def params_for_names():
    st.markdown("#### Parámetros (Nombres – KG + Wikipedia)")
    st.caption("Subí un CSV (una columna de nombres) **o** pegá nombres (uno por línea). Opcional: una columna de ubicación por fila.")

    up = st.file_uploader("CSV de nombres (UTF-8). Si tiene varias columnas, elegí la que corresponde:", type=["csv"], key="names_csv")
    df = _load_names_from_csv(up)

    items_from_csv = []
    names_from_text = []

    csv_col = None
    loc_col = None

    if df is not None:
        cols = list(df.columns)
        # Columna de nombres candidata
        default_idx = 0
        for i, c in enumerate(cols):
            cl = str(c).strip().lower()
            if cl in ("nombre", "nombres", "name", "names", "persona", "entidad"):
                default_idx = i; break
        csv_col = st.selectbox("Columna con los nombres:", cols, index=default_idx, key="names_csv_col")

        # Columna opcional de ubicación
        loc_opt = ["(ninguna)"] + cols
        loc_pick = st.selectbox("Columna de ubicación (opcional):", loc_opt, index=0, key="names_loc_col")
        loc_col = None if loc_pick == "(ninguna)" else loc_pick

        if csv_col:
            try:
                for i, row in df.iterrows():
                    name_val = str(row[csv_col]).strip()
                    if not name_val:
                        continue
                    loc_val = ""
                    if loc_col:
                        try:
                            loc_val = str(row[loc_col]).strip()
                        except Exception:
                            loc_val = ""
                    items_from_csv.append({"name": name_val, "row_location": loc_val})
            except Exception:
                items_from_csv = []

        with st.expander("Vista previa del CSV (primeras 50 filas)"):
            st.dataframe(df.head(50), use_container_width=True)

    names_text = st.text_area("O pegá nombres (uno por línea):", value="", height=160, key="names_textarea")
    names_from_text = [{"name": ln.strip(), "row_location": ""} for ln in names_text.splitlines() if ln.strip()]

    # Unión + dedup por (name,row_location)
    dedup = st.checkbox("Eliminar duplicados exactos (nombre + ubicación por fila)", value=True, key="names_dedup")
    seen = set()
    merged_items = []
    for src in (items_from_csv, names_from_text):
        for it in src:
            key = (it["name"], it.get("row_location",""))
            if (not dedup) or key not in seen:
                if dedup:
                    seen.add(key)
                merged_items.append(it)

    st.caption(f"Total de filas a analizar: **{len(merged_items)}**")

    c1, c2, c3 = st.columns([1,1,1])
    with c1:
        lang = st.selectbox("Idioma (para KG/Wiki)", ["es","en","pt","fr","it","de"], index=0, key="names_lang")
    with c2:
        # Modo de coincidencia por si querés afinar (queda oculto en UI si no se usa)
        match_mode = st.selectbox("Modo de coincidencia", ["Flexible (recomendado)", "Estricto"], index=0, key="names_match_mode")
    with c3:
        strategy = st.selectbox("Estrategia", ["Balance (KG + Wikipedia)"], index=0, key="names_strategy")

    # NUEVO: Tipo de entidad (global) + Inferir automáticamente
    entity_type_label = st.selectbox(
        "Tipo de entidad (global)",
        ["Inferir automáticamente", "Persona", "Organización", "Lugar", "Evento", "Obra creativa", "Producto", "Otro"],
        index=0, key="names_entity_type"
    )

    # NUEVO: Términos complementarios globales
    global_terms = st.text_input(
        "Términos complementarios (globales, opcional)",
        value="", placeholder="ej.: periodista argentino, club de fútbol, banda de rock",
        key="names_global_terms"
    )

    # Sesgo de ubicación (global)
    location_bias = st.text_input(
        "Sesgo de ubicación (global, opcional)",
        value="", placeholder="ej.: Argentina, Madrid (España), Ciudad de México",
        key="names_location_bias"
    )

    # API key de KG (puede venir de secrets o env)
    kg_key = (
        st.secrets.get("kg_api_key")
        or st.secrets.get("google", {}).get("kg_api_key")
        or (st.secrets.get("kg", {}).get("api_key") if "kg" in st.secrets else None)
        or os.getenv("KG_API_KEY")
    )
    if not kg_key:
        st.info("ℹ️ Podés configurar `kg_api_key` en *Secrets* (ideal en `[google]`) o `KG_API_KEY` como variable de entorno. Sin eso, el análisis usará solo Wikipedia.")

    # Normalizar params
    match_mode_val = "strict" if match_mode.startswith("Estricto") else "flex"
    entity_type_map = {
        "Inferir automáticamente": "infer",
        "Persona": "person",
        "Organización": "organization",
        "Lugar": "place",
        "Evento": "event",
        "Obra creativa": "creativeWork",
        "Producto": "product",
        "Otro": "other",
    }

    return {
        "items": merged_items,              # [{name, row_location}]
        "lang": lang,
        "strategy": "balance",
        "kg_api_key": kg_key or "",
        "location_bias": location_bias or "",
        "entity_type": entity_type_map.get(entity_type_label, "infer"),
        "match_mode": match_mode_val,
        "global_terms": global_terms or "",
    }

# ============ ACTIVITY LOG (helpers) ============
def _extract_medio_name(site_url: str | None) -> str | None:
    if not site_url:
        return None
    s = site_url.strip()
    if s.lower().startswith("sc-domain:"):
        return s.split(":", 1)[1].strip() or None
    return None

def _maybe_prefix_sheet_name_with_medio(drive_service, file_id: str, site_url: str):
    medio = _extract_medio_name(site_url)
    if not medio:
        return
    medio = medio.strip().strip("-–—").strip()
    try:
        meta = drive_service.files().get(fileId=file_id, fields="name").execute()
        current = (meta.get("name") or "").strip()
        if re.match(rf"^{re.escape(medio)}\s*[-–—]\s+", current, flags=re.IGNORECASE):
            return
        current_no_lead = re.sub(r"^\s*[-–—]+\s*", "", current)
        new_name = f"{medio} - {current_no_lead}".strip()
        drive_service.files().update(fileId=file_id, body={"name": new_name}).execute()
    except Exception:
        pass

def _get_activity_log_config():
    cfg = st.secrets.get("activity_log", {}) or {}
    return {
        "title": cfg.get("title") or "Nomadic SEO – Activity Log",
        "worksheet": cfg.get("worksheet") or "Log",
        "file_id": cfg.get("file_id") or None,
        "folder_id": cfg.get("folder_id") or st.session_state.get("dest_folder_id"),
    }

def _get_or_create_activity_log_ws(drive, gsclient):
    cfg = _get_activity_log_config()
    file_id = cfg["file_id"]
    title = cfg["title"]
    ws_name = cfg["worksheet"]
    folder_id = cfg["folder_id"]

    try:
        if file_id:
            sh = gsclient.open_by_key(file_id)
        else:
            q = f"name = '{title}' and mimeType = 'application/vnd.google-apps.spreadsheet' and trashed = false"
            res = drive.files().list(
                q=q, spaces="drive",
                fields="files(id,name)",
                includeItemsFromAllDrives=True, supportsAllDrives=True
            ).execute()
            files = res.get("files", [])
            if files:
                file_id = files[0]["id"]
            else:
                body = {"name": title, "mimeType": "application/vnd.google-apps.spreadsheet"}
                if folder_id:
                    body["parents"] = [folder_id]
                new_file = drive.files().create(
                    body=body, fields="id", supportsAllDrives=True
                ).execute()
                file_id = new_file["id"]
        sh = gsclient.open_by_key(file_id)

        try:
            ws = sh.worksheet(ws_name)
        except Exception:
            try:
                ws = sh.sheet1
                ws.update_title(ws_name)
            except Exception:
                ws = sh.add_worksheet(title=ws_name, rows=1000, cols=20)

        headers = ["timestamp", "user_email", "event", "site_url", "analysis_kind", "sheet_id", "sheet_name", "sheet_url", "gsc_account", "notes"]
        try:
            top_left = ws.acell("A1").value
        except Exception:
            top_left = None
        if (top_left or "").strip().lower() != "timestamp":
            try:
                ws.clear()
            except Exception:
                pass
            ws.append_row(headers, value_input_option="USER_ENTERED")
        return ws, file_id
    except Exception:
        return None, None

def _activity_log_append(drive, gsclient, *, user_email: str, event: str,
                         site_url: str = "", analysis_kind: str = "",
                         sheet_id: str = "", sheet_name: str = "", sheet_url: str = "",
                         gsc_account: str = "", notes: str = "") -> None:
    try:
        ws, _ = _get_or_create_activity_log_ws(drive, gsclient)
        if not ws:
            return
        ts = datetime.now().isoformat(timespec="seconds")
        row = [ts, user_email or "", event or "", site_url or "", analysis_kind or "",
               sheet_id or "", sheet_name or "", sheet_url or "", gsc_account or "", notes or ""]
        ws.append_row(row, value_input_option="USER_ENTERED")
    except Exception:
        pass
# ============ /ACTIVITY LOG ============

# ===== Helper para mostrar errores de Google (MOVIDO ARRIBA para uso temprano) =====
def _show_google_error(e, where: str = ""):
    status = None
    try:
        status = getattr(getattr(e, "resp", None), "status", None)
    except Exception:
        pass

    raw = ""
    try:
        raw = getattr(e, "response", None).text
    except Exception:
        pass
    if not raw:
        try:
            raw_bytes = getattr(e, "content", None)
            if raw_bytes:
                raw = raw_bytes.decode("utf-8", "ignore")
        except Exception:
            pass
    if not raw:
        raw = str(e)

    raw_l = raw.lower()
    looks_html = ("<html" in raw_l) or ("<!doctype html" in raw_l)
    is_5xx = False
    try:
        is_5xx = bool(status) and int(status) >= 500
    except Exception:
        pass

    if looks_html or is_5xx:
        st.error(
            f"Google devolvió un **{status or '5xx'}** temporal{f' en {where}' if where else ''}. "
            "Suele resolverse reintentando en breve. Si persiste, probá más tarde."
        )
        with st.expander("Detalle técnico del error"):
            st.code(raw, language="html")
        return

    try:
        data = json.loads(raw)
        msg = (data.get("error") or {}).get("message") or raw
        st.error(f"Google API error{f' en {where}' if where else ''}: {msg}")
        st.code(json.dumps(data, indent=2, ensure_ascii=False), language="json")
    except Exception:
        st.error(f"Google API error{f' en {where}' if where else ''}:")
        st.code(raw)

# --- Ejecutar (MOVIDO ARRIBA para estar disponible en la rama de Nombres) ---
def run_with_indicator(titulo: str, fn, *args, **kwargs):
    mensaje = f"⏳ {titulo}… Esto puede tardar varios minutos."
    if hasattr(st, "status"):
        with st.status(mensaje, expanded=True) as status:
            try:
                res = fn(*args, **kwargs)
                status.update(label="✅ Informe generado", state="complete")
                return res
            except GspreadAPIError as e:
                status.update(label="❌ Error de Google Sheets", state="error")
                _show_google_error(e, where=titulo)
                st.stop()
            except HttpError as e:
                status.update(label="❌ Error de Google API", state="error")
                _show_google_error(e, where=titulo)
                st.stop()
            except Exception as e:
                status.update(label="❌ Error inesperado", state="error")
                st.exception(e)
                st.stop()
    else:
        with st.spinner(mensaje):
            try:
                return fn(*args, **kwargs)
            except GspreadAPIError as e:
                _show_google_error(e, where=titulo)
                st.stop()
            except HttpError as e:
                _show_google_error(e, where=titulo)
                st.stop()
            except Exception as e:
                st.exception(e)
                st.stop()

# --- Resumen con IA (MOVIDO ARRIBA: lo invocamos en la rama de Nombres) ---
def _gemini_summary(sid: str, kind: str, force_prompt_key: str | None = None, widget_suffix: str = "main"):
    st.divider()
    use_ai = st.toggle(
        "Generar resumen con IA (Nomadic Bot 🤖)",
        value=False,
        help="Usa Gemini para leer el Google Sheet y crear un resumen breve y accionable.",
        key=f"ai_summary_toggle_{kind}_{sid}_{widget_suffix}"
    )
    if not use_ai:
        return

    if _AI_IMPORT_ERR:
        st.warning("No pude cargar prompts de ai_summaries; usaré fallback automático.")
    elif _AI_SRC != "none":
        st.caption(f"Fuente de prompts: **{_AI_SRC}**")

    if not is_gemini_configured():
        st.info("🔐 Configurá tu API key de Gemini en Secrets (`GEMINI_API_KEY` o `[gemini].api_key`).")
        return

    def _looks_unsupported(md: str) -> bool:
        if not isinstance(md, str):
            return False
        low = md.lower()
        needles = [
            "por ahora solo está implementado el resumen para auditoría de tráfico",
            "solo está implementado el resumen para auditoría",
            "only the traffic audit summary is implemented",
            "only audit summary is implemented",
            "aún no implementado",
            "not yet implemented",
            "tipo aun no es soportado",
        ]
        return any(n in low for n in needles)

    prompt_used = None
    prompt_key = force_prompt_key or kind
    prompt_source = "fallback"

    try:
        if _SUMMARIZE_WITH_PROMPT and _PROMPTS and (prompt_key in _PROMPTS):
            prompt_used = _PROMPTS[prompt_key]
            prompt_source = f"{_AI_SRC}:{prompt_key}"
    except Exception:
        pass

    try:
        if _SUMMARIZE_WITH_PROMPT and (prompt_used is not None):
            with st.spinner(f"🤖 Nomadic Bot está leyendo tu informe (prompt: {prompt_source})…"):
                md = _SUMMARIZE_WITH_PROMPT(gs_client, sid, kind=prompt_key, prompt=prompt_used)
        else:
            with st.spinner("🤖 Nomadic Bot está leyendo tu informe (modo automático)…"):
                md = summarize_sheet_auto(gs_client, sid, kind=kind)

        if _looks_unsupported(md):
            with st.spinner("🤖 El tipo reportó no estar soportado; reintentando en modo fallback…"):
                md = summarize_sheet_auto(gs_client, sid, kind=kind)

        st.caption(f"🧠 Prompt en uso: **{prompt_source}**")
        render_summary_box(md)

    except Exception as e:
        st.error(
            f"Falló el resumen con prompt específico **({prompt_source})**; "
            f"usaré fallback automático.\n\n**Motivo:** {repr(e)}"
        )
        with st.spinner("🤖 Usando fallback…"):
            md = summarize_sheet_auto(gs_client, sid, kind=kind)
        st.caption("🧠 Prompt en uso: **fallback:auto**")
        render_summary_box(md)

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
    for k in ("creds_src", "oauth_src", "step3_done", "src_account_label"):
        st.session_state.pop(k, None)
    st.session_state.pop("sc_account_choice", None)
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
        _activity_log_append(
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
                sid = None
                # Ejecutar
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

                _activity_log_append(
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
                _gemini_summary(sid, kind="names", widget_suffix="after_run")

    # Panel persistente de resumen si ya hay algo
    if st.session_state.get("last_file_id") and st.session_state.get("last_file_kind"):
        st.divider()
        st.subheader("📄 Resumen del análisis")
        st.caption("Podés generar o regenerar el resumen sin volver a ejecutar el análisis.")
        _gemini_summary(
            st.session_state["last_file_id"],
            kind=st.session_state["last_file_kind"],
            widget_suffix="panel"
        )

    # Fin rama "7" (evita renderizar GSC y sitio)
    if True:
        # Debug opcional (solo si está activo)
        if st.session_state.get("DEBUG"):
            st.write(
                "¿Gemini listo?",
                "GEMINI_API_KEY" in st.secrets or ("gemini" in st.secrets and "api_key" in st.secrets["gemini"])
            )
        st.stop()

# ======== Resto de análisis (sí requieren GSC) ========

# --- PASO 3: Conectar Search Console (fuente de datos) ---
def _has_gsc_scope(scopes: list[str] | None) -> bool:
    if not scopes:
        return False
    needed = set(SCOPES_GSC)
    return any(s in scopes for s in needed) or "https://www.googleapis.com/auth/webmasters" in scopes

def _norm(s: str | None) -> str:
    if not s: return ""
    return "".join(ch for ch in s.lower() if ch.isalnum())

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

    if not _has_gsc_scope(creds_dest_dict.get("scopes")):
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
    wanted_norm = _norm(sc_choice)
    have_label = st.session_state.get("src_account_label")
    have_norm = _norm(have_label)
    need_new_auth = (
        not st.session_state.get("step3_done")
        or (have_norm != wanted_norm)
        or (have_norm == _norm("Acceso en cuenta personal de Nomadic"))
    )

    if need_new_auth:
        for k in ("creds_src", "oauth_src", "step3_done", "src_account_label"):
            st.session_state.pop(k, None)

        st.info(f"Conectá la cuenta **{sc_choice}** para Search Console.")
        creds_src_obj = pick_source_oauth()
        if not creds_src_obj:
            st.stop()

        picked_label = (st.session_state.get("oauth_src") or {}).get("account") or ""
        picked_norm = _norm(picked_label)

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
                    <a href="{APP_HOME}?action=change_src" target="_self" rel="nofollow">(Cambiar cuenta de acceso)</a>
                </div>
                ''',
                unsafe_allow_html=True
            )
        except Exception as e:
            st.error(f"No pude inicializar el cliente de Search Console: {e}")
            st.stop()

# --- PASO 4: sitio + PASO 5: análisis ---
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
                    _render_prompt_probe(kind="core", force_key="core")
                else:
                    st.caption(f"Fuente actual de prompts: {_AI_SRC}")
                with st.expander("🧪 Diagnóstico Gemini", expanded=False):
                    if st.button("Probar SDK Gemini", key="probe_gemini"):
                        ok, msgs = _gemini_healthcheck()
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
            _maybe_prefix_sheet_name_with_medio(drive_service, sid, site_url)

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
            _activity_log_append(
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
            _gemini_summary(sid, kind="core", force_prompt_key="core", widget_suffix="after_run")

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
            _maybe_prefix_sheet_name_with_medio(drive_service, sid, site_url)

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
            _activity_log_append(
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
            _gemini_summary(sid, kind="evergreen", widget_suffix="after_run")

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
            _maybe_prefix_sheet_name_with_medio(drive_service, sid, site_url)

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
            _activity_log_append(
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
            _gemini_summary(sid, kind="audit", widget_suffix="after_run")

else:
    st.info("Las opciones 1, 2 y 3 aún no están disponibles en esta versión.")

# --- Panel persistente para generar resumen del último informe sin rerun del análisis ---
if st.session_state.get("last_file_id") and st.session_state.get("last_file_kind"):
    st.divider()
    st.subheader("📄 Resumen del análisis")
    st.caption("Podés generar o regenerar el resumen sin volver a ejecutar el análisis.")
    _gemini_summary(
        st.session_state["last_file_id"],
        kind=st.session_state["last_file_kind"],
        force_prompt_key="core" if st.session_state["last_file_kind"] == "core" else None,
        widget_suffix="panel"
    )

# Debug opcional (solo si está activo)
if st.session_state.get("DEBUG"):
    st.write(
        "¿Gemini listo?",
        "GEMINI_API_KEY" in st.secrets or ("gemini" in st.secrets and "api_key" in st.secrets["gemini"])
    )