# modules/app_ai.py
import os
import importlib.util
import pathlib
import streamlit as st

from app_config import _resolve_gemini_model  # solo para healthcheck
try:
    from .app_ext import EXT_PACKAGE
except Exception:
    # Fallback por si app.py ya mapeó 'app_ext' en sys.modules
    from app_ext import EXT_PACKAGE  # type: ignore

from modules.ai import is_gemini_configured, summarize_sheet_auto, render_summary_box

# Estado interno (accesible vía getters)
_SUMMARIZE_WITH_PROMPT = None
_PROMPTS = None
_AI_SRC = "none"
_AI_IMPORT_ERR = None


def load_prompts():
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
        if EXT_PACKAGE:
            base = pathlib.Path(EXT_PACKAGE.__file__).parent
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


def ai_source() -> str:
    return _AI_SRC


def ai_import_error():
    return _AI_IMPORT_ERR


def prompts_map():
    return _PROMPTS


def gemini_healthcheck():
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


def render_prompt_probe(kind: str, force_key: str | None = None):
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


def gemini_summary(gs_client, sid: str, kind: str, force_prompt_key: str | None = None, widget_suffix: str = "main"):
    """Genera un resumen IA con UI y devuelve texto."""
    if _AI_IMPORT_ERR:
        st.warning("No pude cargar prompts de ai_summaries; usaré fallback automático.")
    elif _AI_SRC != "none":
        st.caption(f"Fuente de prompts: **{_AI_SRC}**")

    if not is_gemini_configured():
        st.info("🔐 Configurá tu API key de Gemini en Secrets (`GEMINI_API_KEY` o `[gemini].api_key`).")
        return ""

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
        return md

    except Exception as e:
        st.error(
            f"Falló el resumen con prompt específico **({prompt_source})**; "
            f"usaré fallback automático.\n\n**Motivo:** {repr(e)}"
        )
        with st.spinner("🤖 Usando fallback…"):
            md = summarize_sheet_auto(gs_client, sid, kind=kind)
        st.caption("🧠 Prompt en uso: **fallback:auto**")
        render_summary_box(md)
        return md


def gemini_summary_text(gs_client, sid: str, kind: str) -> str:
    """Devuelve solo el texto del resumen (sin UI)."""
    prompt_key = kind
    prompt_used = None

    if _SUMMARIZE_WITH_PROMPT and _PROMPTS and (prompt_key in _PROMPTS):
        prompt_used = _PROMPTS[prompt_key]

    try:
        if _SUMMARIZE_WITH_PROMPT and prompt_used:
            return _SUMMARIZE_WITH_PROMPT(gs_client, sid, kind=prompt_key, prompt=prompt_used) or ""
        else:
            return summarize_sheet_auto(gs_client, sid, kind=kind) or ""
    except Exception:
        return summarize_sheet_auto(gs_client, sid, kind=kind) or ""
