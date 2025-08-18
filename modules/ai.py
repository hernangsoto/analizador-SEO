# modules/ai.py
from __future__ import annotations

import os
import re
import math
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st

# Intento opcional de usar tabulate; si no está, hacemos fallback manual
try:
    from tabulate import tabulate as _tabulate  # type: ignore
except Exception:
    _tabulate = None

# --------------------------------------------------------------------------------------
# Configuración de Gemini
# --------------------------------------------------------------------------------------

def _get_gemini_api_key() -> Optional[str]:
    # Soporta dos ubicaciones en secrets
    key = st.secrets.get("GEMINI_API_KEY")
    if not key:
        key = st.secrets.get("gemini", {}).get("api_key")
    if not key:
        # Fallback a variable de entorno (opcional)
        key = os.environ.get("GEMINI_API_KEY")
    return key

def _get_gemini_model_name() -> str:
    return st.secrets.get("gemini", {}).get("model", "gemini-1.5-flash")

def _load_gemini():
    try:
        import google.generativeai as genai  # type: ignore
    except Exception as e:
        raise RuntimeError(
            "Falta el paquete 'google-generativeai'. Instalá con: "
            "`pip install google-generativeai`"
        ) from e
    api_key = _get_gemini_api_key()
    if not api_key:
        raise RuntimeError(
            "No encontré la API key de Gemini. "
            "Definí `GEMINI_API_KEY` en Streamlit Secrets (o en [gemini].api_key)."
        )
    genai.configure(api_key=api_key)
    return genai

def is_gemini_configured() -> bool:
    try:
        _load_gemini()
        return True
    except Exception:
        return False

# --------------------------------------------------------------------------------------
# Helpers de lectura (gspread client + Google Sheet)
# --------------------------------------------------------------------------------------

def _ws_try(sheet, names: List[str]):
    """
    Devuelve la primera worksheet cuyo título matchee alguno de 'names' (flexible).
    """
    def _norm(s: str) -> str:
        s = s.lower()
        s = re.sub(r"[|]", " ", s)
        s = re.sub(r"\s+", " ", s).strip()
        s = re.sub(r"[^a-z0-9 ]", "", s)
        return s.replace(" ", "")

    wanted = {_norm(n) for n in names if n}
    for ws in sheet.worksheets():
        t = _norm(ws.title)
        if t in wanted or any(t in w or w in t for w in wanted):
            return ws
    return None

def _read_table(ws, start_row: int, n_cols: int, max_rows: int = 5000) -> pd.DataFrame:
    """
    Lee un bloque tipo tabla comenzando en start_row, recorta en fila vacía.
    Asume que no hay encabezado en start_row (son datos directos).
    """
    values = ws.get_all_values()
    # Normaliza índice base-1 de Sheets a base-0 de Python
    data = values[start_row - 1 : start_row - 1 + max_rows]
    def _is_blank_row(row):
        return not any(str(x).strip() for x in row[:n_cols])
    out = []
    for r in data:
        row = (r + [""] * n_cols)[:n_cols]
        if _is_blank_row(row):
            break
        out.append(row)
    df = pd.DataFrame(out)
    return df

def _to_num(x):
    try:
        # soporta "1,234.5" o "1.234,5" básicos
        s = str(x).strip()
        if s == "" or s.lower() in ("nan", "none", "null"):
            return 0.0
        # reemplazos simples de separadores
        if "," in s and "." in s:
            if s.find(".") < s.find(","):
                s = s.replace(".", "").replace(",", ".")
            else:
                s = s.replace(",", "")
        else:
            s = s.replace(",", ".")
        return float(s)
    except Exception:
        try:
            return float(x)
        except Exception:
            return 0.0

# --------------------------------------------------------------------------------------
# Conversión DataFrame -> Markdown (con fallback si no hay tabulate)
# --------------------------------------------------------------------------------------

def _df_to_md(d: Optional[pd.DataFrame], max_rows: int = 60) -> str:
    """
    Convierte un DataFrame a tabla Markdown.
    - Usa 'tabulate' si está disponible.
    - Si no, genera una tabla Markdown manualmente.
    - Limita filas para no inflar el prompt del modelo.
    """
    if d is None or d.empty:
        return "_(sin datos)_"

    df = d.copy()
    if max_rows and len(df) > max_rows:
        df = df.head(max_rows)

    def _fmt(v):
        if v is None:
            return ""
        # pandas NaN / NaT
        try:
            if pd.isna(v):
                return ""
        except Exception:
            pass
        # fechas
        if isinstance(v, pd.Timestamp):
            try:
                return v.date().isoformat()
            except Exception:
                return v.isoformat()
        # floats seguros
        if isinstance(v, float):
            if not math.isfinite(v):
                return ""
            s = f"{v:.4f}".rstrip("0").rstrip(".")
            return s if s != "-0" else "0"
        return str(v)

    data = [[_fmt(v) for v in row] for row in df.itertuples(index=False)]

    if _tabulate:
        # formato tipo GitHub
        return _tabulate(data, headers=list(df.columns), tablefmt="github")

    # Fallback manual a Markdown
    headers = [str(c) for c in df.columns]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in data:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)

# --------------------------------------------------------------------------------------
# Construcción del prompt y llamada a Gemini
# --------------------------------------------------------------------------------------

def _build_prompt_traffic_audit(config: Dict[str, Any],
                                totales_df: Optional[pd.DataFrame],
                                search_daily: Optional[pd.DataFrame],
                                discover_daily: Optional[pd.DataFrame],
                                top15_search: Optional[pd.DataFrame],
                                top15_discover: Optional[pd.DataFrame]) -> str:
    """
    Arma un prompt compacto en español para que Gemini redacte el resumen.
    """
    cfg_lines = []
    for k in ["Sitio Analizado", "Modo de período", "Períodos PREVIOS incluidos",
              "Lag de datos (días)", "Origen de datos", "Sección", "Ámbito", "País",
              "Rango total (para diarios)"]:
        v = config.get(k, "")
        cfg_lines.append(f"- **{k}**: {v}")
    cfg_md = "\n".join(cfg_lines)

    prompt = f"""
Eres **Nomadic Bot 🤖**, un analista SEO que responde en español de forma clara y accionable.
Te doy datos extraídos de Search Console para una **Auditoría de tráfico**.

## Contexto
{cfg_md}

## Totales por período
{_df_to_md(totales_df)}

## Datos diarios (Search)
{_df_to_md(search_daily)}

## Datos diarios (Discover)
{_df_to_md(discover_daily)}

## Top 15 páginas (Search)
{_df_to_md(top15_search, max_rows=15)}

## Top 15 páginas (Discover)
{_df_to_md(top15_discover, max_rows=15)}

### Instrucciones
1) Da un **panorama general** de rendimiento del período actual vs previos (crecimiento/caída, CTR, estacionalidad).
2) Lista **oportunidades y riesgos** (páginas o secciones con señales claras).
3) Propón **acciones concretas** (on-page, contenidos, enlazado interno, quick wins).
4) Si hay Search y Discover, **diferencia** el aporte de cada canal.
5) Mantén el tono profesional, breve y estructurado con viñetas y subtítulos.
"""
    return prompt

def _call_gemini(prompt: str) -> str:
    genai = _load_gemini()
    model_name = _get_gemini_model_name()
    model = genai.GenerativeModel(model_name)
    resp = model.generate_content(prompt)
    try:
        text = (resp.text or "").strip()
    except Exception:
        # SDKs antiguos devuelven candidates
        try:
            text = (resp.candidates[0].content.parts[0].text or "").strip()  # type: ignore
        except Exception:
            text = ""
    return text or "No se pudo generar el resumen en este intento."

# --------------------------------------------------------------------------------------
# Summary principal (Auditoría de tráfico)
# --------------------------------------------------------------------------------------

def summarize_traffic_audit_from_sheet(gs_client, spreadsheet_id: str) -> str:
    """
    Lee las pestañas del template de Auditoría de tráfico y genera
    un resumen con Gemini (Nomadic Bot 🤖). Devuelve **Markdown**.
    """
    try:
        sh = gs_client.open_by_key(spreadsheet_id)
    except Exception as e:
        return f"❌ No pude abrir el Sheet ({spreadsheet_id}): {e}"

    # 1) Configuración
    config_ws = _ws_try(sh, ["Configuración", "Configuracion", "Settings"])
    config_dict: Dict[str, Any] = {}
    if config_ws:
        vals = config_ws.get_all_values()
        # esperamos key/valor a partir de la fila 2 (fila 1 suele ser encabezado)
        for row in vals[1:]:
            if len(row) >= 2 and (row[0] or row[1]):
                config_dict[row[0].strip()] = row[1].strip()
    else:
        config_dict["Sitio Analizado"] = "(desconocido)"

    # 2) Totales por período (primer bloque desde fila 2, hasta fila en blanco)
    tot_ws = _ws_try(sh, ["Totales por período", "Totales por periodo", "Trafico por periodo"])
    tot_df = None
    if tot_ws:
        tdf = _read_table(tot_ws, start_row=2, n_cols=6, max_rows=3000)
        if not tdf.empty:
            tdf.columns = ["periodo", "inicio", "fin", "clics", "impresiones", "ctr"]
            for c in ["clics", "impresiones", "ctr"]:
                tdf[c] = tdf[c].apply(_to_num)
            tot_df = tdf

    # 3) Datos diarios
    sd_ws = _ws_try(sh, ["Search | Datos Diarios", "Search Datos Diarios"])
    sd_df = None
    if sd_ws:
        sdf = _read_table(sd_ws, start_row=2, n_cols=3, max_rows=4000)
        if not sdf.empty:
            sdf.columns = ["fecha", "clics", "impresiones"]
            sdf["clics"] = sdf["clics"].apply(_to_num)
            sdf["impresiones"] = sdf["impresiones"].apply(_to_num)
            sd_df = sdf

    dd_ws = _ws_try(sh, ["Discover | Datos Diarios", "Discover Datos Diarios"])
    dd_df = None
    if dd_ws:
        ddf = _read_table(dd_ws, start_row=2, n_cols=3, max_rows=4000)
        if not ddf.empty:
            ddf.columns = ["fecha", "clics", "impresiones"]
            ddf["clics"] = ddf["clics"].apply(_to_num)
            ddf["impresiones"] = ddf["impresiones"].apply(_to_num)
            dd_df = ddf

    # 4) Top 15 (Search en fila 3, Discover en fila 21 según tu template)
    top_ws = _ws_try(sh, ["Top 15", "Top-15", "Top15"])
    top_s_df = None
    top_d_df = None
    if top_ws:
        # Search
        tdf_s = _read_table(top_ws, start_row=3, n_cols=6, max_rows=20)
        if not tdf_s.empty:
            tdf_s.columns = ["url", "sección", "clics", "impresiones", "ctr", "posición"]
            for c in ["clics", "impresiones", "ctr", "posición"]:
                tdf_s[c] = tdf_s[c].apply(_to_num)
            top_s_df = tdf_s
        # Discover
        tdf_d = _read_table(top_ws, start_row=21, n_cols=6, max_rows=20)
        if not tdf_d.empty:
            tdf_d.columns = ["url", "sección", "clics", "impresiones", "ctr", "posición"]
            for c in ["clics", "impresiones", "ctr", "posición"]:
                tdf_d[c] = tdf_d[c].apply(_to_num)
            top_d_df = tdf_d

    # 5) Prompt + Gemini
    prompt = _build_prompt_traffic_audit(config=config_dict,
                                         totales_df=tot_df,
                                         search_daily=sd_df,
                                         discover_daily=dd_df,
                                         top15_search=top_s_df,
                                         top15_discover=top_d_df)

    try:
        summary = _call_gemini(prompt)
    except Exception as e:
        return f"❌ No pude generar el resumen con Gemini: {e}"

    # 6) Caja con título (opcional estilizado)
    head = "### 🤖 Nomadic Bot — Resumen de Auditoría de tráfico\n"
    return head + "\n" + summary

# --------------------------------------------------------------------------------------
# Render helper (Streamlit)
# --------------------------------------------------------------------------------------

def render_summary_box(markdown_text: str) -> None:
    """
    Renderiza el resumen en una caja neutra.
    """
    if not markdown_text:
        return
    st.markdown(
        """
        <style>
        .nbot-box {
          border: 1px solid #E5E7EB;
          background: #F9FAFB;
          border-radius: 10px;
          padding: 16px 18px;
          margin: 6px 0 18px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(f'<div class="nbot-box">{markdown_text}</div>', unsafe_allow_html=True)

# --------------------------------------------------------------------------------------
# Hooks de alto nivel para distintos informes (si luego amplías)
# --------------------------------------------------------------------------------------

def summarize_sheet_auto(gs_client, spreadsheet_id: str, kind: str = "audit") -> str:
    """
    Router simple para distintos tipos de sheet (por ahora 'audit').
    """
    if kind == "audit":
        return summarize_traffic_audit_from_sheet(gs_client, spreadsheet_id)
    # Podrías añadir: 'evergreen', 'core_update', etc.
    return "Por ahora solo está implementado el resumen para Auditoría de tráfico."

# --------------------------------------------------------------------------------------
# Uso (ejemplo desde app.py)
# --------------------------------------------------------------------------------------
# from modules.ai import is_gemini_configured, summarize_sheet_auto, render_summary_box
# if is_gemini_configured() and st.session_state.get("last_file_id"):
#     md = summarize_sheet_auto(gs_client, st.session_state["last_file_id"], kind="audit")
#     render_summary_box(md)
