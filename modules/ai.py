# modules/ai.py
from __future__ import annotations

import os
import re
import unicodedata
from typing import Iterable, Optional

import pandas as pd
import streamlit as st

# -----------------------------
# Helpers básicos (gspread)
# -----------------------------

def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = re.sub(r"\s+", " ", s.replace("|", " ").strip()).lower()
    s = re.sub(r"[^a-z0-9 ]", "", s).replace(" ", "")
    return s

def _get_ws(sh, names: Iterable[str]) -> Optional[object]:
    targets = {_norm(n) for n in names if n}
    for ws in sh.worksheets():
        if _norm(ws.title) in targets:
            return ws
    return None

def _read_table(ws, start_cell: str = "A1", max_rows: int = 1000, max_cols: int = 10) -> pd.DataFrame:
    """Lee un bloque desde start_cell, recorta filas/cols vacías y devuelve DataFrame."""
    # Range amplio para no traer toda la hoja
    end_col = chr(ord("A") + max_cols - 1)
    rng = f"{start_cell}:{end_col}{max_rows}"
    values = ws.get(rng) or []
    if not values:
        return pd.DataFrame()
    # La primera fila es el header; recorta columnas vacías al final
    # y filas 100% vacías.
    # Normaliza ancho
    width = max(len(r) for r in values)
    values = [r + [""] * (width - len(r)) for r in values]
    # Quita tail vacío
    def not_all_empty(row): return any(str(c).strip() != "" for c in row)
    values = [r for r in values if not_all_empty(r)]
    if not values:
        return pd.DataFrame()

    header, rows = values[0], values[1:]
    df = pd.DataFrame(rows, columns=header)
    # Quita columnas completamente vacías
    df = df.loc[:, (df.astype(str).applymap(lambda x: x.strip()) != "").any()]
    return df

def _to_float(s) -> float:
    try:
        if s is None: return 0.0
        if isinstance(s, (int, float)): return float(s)
        s = str(s).replace("%", "").replace(",", ".").strip()
        return float(s) if s else 0.0
    except Exception:
        return 0.0

def _to_date(s):
    try:
        return pd.to_datetime(s).date()
    except Exception:
        return s

# -----------------------------
# Gemini client
# -----------------------------

def _get_gemini_model(model_name: str = "gemini-1.5-flash"):
    """Devuelve un cliente de Gemini ya configurado o None si no hay API key."""
    api_key = st.secrets.get("GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None, "Falta configurar GEMINI_API_KEY en secrets o variables de entorno."
    import google.generativeai as genai  # import lazy
    genai.configure(api_key=api_key)
    try:
        model = genai.GenerativeModel(model_name)
        return model, None
    except Exception as e:
        return None, f"No pude iniciar Gemini: {e}"

# -----------------------------
# Lectura de datos del Sheet
# -----------------------------

def _read_blocks_for_audit(gs_client, sid: str):
    """Intenta leer los bloques estándar que produce el análisis de Auditoría."""
    sh = gs_client.open_by_key(sid)

    ws_tot = _get_ws(sh, ["Totales por período", "Totales por periodo", "Trafico por periodo"])
    ws_top15 = _get_ws(sh, ["Top 15", "Top-15", "Top15"])
    ws_s_daily = _get_ws(sh, ["Search | Datos Diarios", "Search Datos Diarios"])
    ws_d_daily = _get_ws(sh, ["Discover | Datos Diarios", "Discover Datos Diarios"])
    ws_s_pages = _get_ws(sh, ["Search | Páginas por período", "Search Paginas por periodo"])
    ws_d_pages = _get_ws(sh, ["Discover | Páginas por período", "Discover Paginas por periodo"])

    # Totales por período (desde fila 2)
    df_tot = pd.DataFrame()
    header_txt = ""
    if ws_tot:
        header = ws_tot.get("A1:A1")
        header_txt = (header[0][0] if header and header[0] else "") or ""
        df_tot = _read_table(ws_tot, start_cell="A2", max_rows=500, max_cols=6)
        # Normaliza nombres si vienen distintos
        df_tot.columns = [c.strip().lower() for c in df_tot.columns]
        df_tot.rename(columns={
            "periodo":"periodo", "inicio":"inicio", "fin":"fin",
            "clics":"clics", "impresiones":"impresiones", "ctr":"ctr"
        }, inplace=True)

        # Cast
        if not df_tot.empty:
            for col in ("clics","impresiones","ctr"):
                if col in df_tot.columns:
                    df_tot[col] = df_tot[col].map(_to_float)
            for col in ("inicio","fin"):
                if col in df_tot.columns:
                    df_tot[col] = df_tot[col].map(_to_date)

    # Top 15 — dos bloques posibles
    df_top_search = pd.DataFrame()
    df_top_disc   = pd.DataFrame()
    if ws_top15:
        # Search: filas 3..18
        df_ts = _read_table(ws_top15, start_cell="A3", max_rows=18, max_cols=6)
        # Discover: filas 21..36
        df_td = _read_table(ws_top15, start_cell="A21", max_rows=36, max_cols=6)
        for d in (df_ts, df_td):
            d.columns = [c.strip().lower() for c in d.columns]
            d.rename(columns={"sección":"seccion","posición":"posicion"}, inplace=True)
            for col in ("clics","impresiones","ctr","posicion"):
                if col in d.columns: d[col] = d[col].map(_to_float)
        df_top_search, df_top_disc = df_ts, df_td

    # Diarios
    df_s_daily = _read_table(ws_s_daily, start_cell="A2", max_rows=5000, max_cols=3) if ws_s_daily else pd.DataFrame()
    df_d_daily = _read_table(ws_d_daily, start_cell="A2", max_rows=5000, max_cols=3) if ws_d_daily else pd.DataFrame()
    for d in (df_s_daily, df_d_daily):
        if not d.empty:
            d.columns = [c.strip().lower() for c in d.columns]
            for col in ("clics","impresiones"):
                if col in d.columns: d[col] = d[col].map(_to_float)
            if "fecha" in d.columns: d["fecha"] = d["fecha"].map(_to_date)

    # Pages por período (para insights extra)
    df_s_pages = _read_table(ws_s_pages, start_cell="A2", max_rows=25000, max_cols=5) if ws_s_pages else pd.DataFrame()
    df_d_pages = _read_table(ws_d_pages, start_cell="A2", max_rows=25000, max_cols=5) if ws_d_pages else pd.DataFrame()
    for d in (df_s_pages, df_d_pages):
        if not d.empty:
            d.columns = [c.strip().lower() for c in d.columns]
            for col in ("clics","impresiones","ctr"):
                if col in d.columns: d[col] = d[col].map(_to_float)

    return {
        "header": header_txt,
        "totales": df_tot,
        "top_search": df_top_search,
        "top_discover": df_top_disc,
        "search_daily": df_s_daily,
        "discover_daily": df_d_daily,
        "search_pages": df_s_pages,
        "discover_pages": df_d_pages,
    }

# -----------------------------
# Construcción de prompt
# -----------------------------

def _mk_small_table(df: pd.DataFrame, max_rows=10) -> str:
    if df is None or df.empty:
        return "_(sin datos)_"
    head = df.head(max_rows).copy()
    # Limita columnas útiles
    if len(head.columns) > 8:
        head = head.iloc[:, :8]
    # To markdown simple
    return head.to_markdown(index=False)

def _build_prompt_audit(site_url: str, modo: str, tipo: str, blocks: dict, periods_back: int) -> str:
    """Crea un prompt compacto en español a partir de los bloques leídos del Sheet."""
    tot = blocks["totales"]
    cur_txt = ""
    if not tot.empty and {"inicio","fin"}.issubset(tot.columns):
        cur_txt = f"{tot.iloc[0]['inicio']} → {tot.iloc[0]['fin']}"
    head_label = blocks["header"] or "Totales por período"

    return f"""
Actúa como un analista SEO senior. Resume de forma breve y accionable (máx. 220–280 palabras)
los hallazgos del siguiente informe de **Auditoría de tráfico** para *{site_url}*.
Responde **en español (tono profesional, claro)**, con bullets, y una sección final de “Próximos pasos”.

**Contexto del análisis**
- Modo del período: {modo}
- Fuente(s): {tipo}
- Periodos previos incluidos: {periods_back}
- Período actual: {cur_txt}

**Totales por período** ({head_label})
{_mk_small_table(tot, 8)}

**Top 15 Search (clics) – actual**
{_mk_small_table(blocks["top_search"], 10)}

**Top 15 Discover (clics) – actual**
{_mk_small_table(blocks["top_discover"], 10)}

**Serie diaria Search (fecha, clics, impresiones) – agregado**
{_mk_small_table(blocks["search_daily"], 12)}

**Serie diaria Discover (fecha, clics, impresiones) – agregado**
{_mk_small_table(blocks["discover_daily"], 12)}

Instrucciones:
1) Compara el período actual vs. el promedio de los previos (clics, impresiones, CTR) y destaca variaciones relevantes (con signo y ~%).
2) Identifica 3–5 **ganadores/perdedores** (URLs o secciones) y posibles causas (ej. estacionalidad, ranking/CTR, cobertura Discover).
3) Propón **3–6 próximos pasos** (rápidos y mediano plazo) con foco en impacto SEO.
4) NO uses tablas, solo bullets y frases cortas. Evita jergas. Nombra “Nomadic Bot 🤖” al encabezado.
"""

# -----------------------------
# Entrada pública
# -----------------------------

def nomadic_ai_summary_audit(gs_client, sid: str, *, site_url: str, modo: str, tipo: str, periods_back: int) -> str:
    """
    Lee el Google Sheet de Auditoría de tráfico (sid) y devuelve un texto en español
    con el resumen generado por Gemini. Maneja faltantes con gracia.
    """
    model, err = _get_gemini_model()
    if not model:
        return f"⚠️ No se pudo iniciar Gemini: {err}"

    blocks = _read_blocks_for_audit(gs_client, sid)
    prompt = _build_prompt_audit(site_url=site_url, modo=modo, tipo=tipo, blocks=blocks, periods_back=periods_back)

    try:
        resp = model.generate_content(prompt)
        text = (resp.text or "").strip()
        if not text:
            return "⚠️ Gemini no devolvió contenido."
        # Etiqueta con marca
        return f"### Nomadic Bot 🤖 — Resumen de Auditoría\n\n{text}"
    except Exception as e:
        return f"⚠️ Error al generar el resumen con Gemini: {e}"
