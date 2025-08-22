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
# >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
# Helpers generales (DEFINIDOS TEMPRANO para evitar NameError)
# ------------------------------------------------------------

# ===== Helper para mostrar errores de Google =====
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

# --- Ejecutar con indicador ---
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

# --- Resumen con IA (prompts por tipo + fallback) ---
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

    if oo.get("use_redirect") and code:
        expected_state = oo.get("flow_state")
        flow = None
        store = _oauth_flow_store()
        if state_in and state_in in store:
            flow = store.pop(state_in)["flow"]
        if not flow:
            st.info("Intentando recuperar sesión…")
            if has_web:
                from google_auth_oauthlib.flow import Flow
                client_secrets = {"web": {
                    "client_id": auth_sec["client_id"], "client_secret": auth_sec["client_secret"],
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                    "redirect_uris": [redirect_uri],
                }}
                flow = Flow.from_client_config(client_secrets, scopes=scopes_step0)
                flow.redirect_uri = redirect_uri

        from urllib.parse import urlencode
        current_url = f"{oo['redirect_uri']}?{urlencode({k: (v[0] if isinstance(v, list) else v) for k, v in qp.items()}, doseq=True)}"
        try:
            if expected_state and state_in and state_in != expected_state:
                st.info("Aviso: el 'state' no coincide (posible nueva pestaña). Usando flujo recuperado…")
            flow.fetch_token(authorization_response=current_url)
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
        url = st.text_input("🔑 Paso B (identidad): pegá la URL completa (http://localhost/?code=...&state=...)", key="auth_response_url_oidc", placeholder="http://localhost/?code=...&state=...")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Verificar identidad", type="primary", key="btn_oidc_connect"):
                if not url.strip():
                    st.error("Pegá la URL completa de redirección (incluye code y state).")
                    st.stop()
                try:
                    flow_state = oo.get("flow_state")
                    store = _oauth_flow_store()
                    flow = None
                    if flow_state and flow_state in store:
                        flow = store.pop(flow_state)["flow"]
                    if not flow:
                        acct_for_dest = st.secrets.get("oauth_app_key", "ACCESO")
                        flow = build_flow(acct_for_dest, scopes_step0)
                    flow.fetch_token(authorization_response=url.strip())
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
def _