# modules/app_params.py
from __future__ import annotations

import os
from datetime import date, timedelta
import pandas as pd
import streamlit as st

# ----------------- Utilidades internas -----------------

LAG_DAYS_DEFAULT = 3

def _parse_paths_csv(txt: str) -> list[str]:
    if not txt:
        return []
    items = [p.strip() for p in txt.split(",")]
    return [p for p in items if p]

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

# ----------------- Parámetros: Core Update -----------------

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

    # Guardamos para que app.py la lea antes de ejecutar el análisis
    st.session_state["core_filters_payload"] = adv_payload

    # Compat: seccion_legacy (solo si es include simple de 1 ruta)
    seccion_legacy = None
    if adv_payload and "sections" in adv_payload:
        if adv_payload["sections"]["mode"] == "include" and len(adv_payload["sections"]["paths"]) == 1 and "subsections" not in adv_payload:
            seccion_legacy = adv_payload["sections"]["paths"][0]
    if (not adv_payload) and sec_list_txt.strip():
        first = _parse_paths_csv(sec_list_txt)[:1]
        seccion_legacy = first[0] if first else None

    return lag_days, core_choice, custom_ini, custom_fin, tipo, pais, seccion_legacy

# ----------------- Parámetros: Evergreen -----------------

def params_for_evergreen():
    st.markdown("#### Parámetros (Evergreen)")
    st.caption("Se usa el período más amplio posible de **meses completos** (hasta 16) en Search.")
    lag_days = st.number_input("Lag de datos (para evitar días incompletos)", 0, 7, LAG_DAYS_DEFAULT, key="lag_ev")

    pais_choice = st.selectbox("¿Filtrar por país? (ISO-3)", ["Todos", "ARG", "MEX", "ESP", "USA", "COL", "PER", "CHL", "URY"], index=0, key="pais_ev")
    pais = None if pais_choice == "Todos" else pais_choice

    seccion = st.text_input("¿Limitar a una sección? (path, ej: /vida/)", value="", key="sec_ev") or None

    # Ventana mensual completa (hasta mes anterior al 'hoy_util')
    hoy_util = date.today() - timedelta(days=lag_days)
    end_month_first_day = (pd.Timestamp(hoy_util.replace(day=1)) - pd.offsets.MonthBegin(1))
    end_month_last_day = (end_month_first_day + pd.offsets.MonthEnd(0))
    start_month_first_day = (end_month_first_day - pd.DateOffset(months=15))
    start_date = start_month_first_day.date()
    end_date = end_month_last_day.date()
    st.info(f"Ventana mensual: {start_date} → {end_date}")

    incluir_diario = st.checkbox("Incluir análisis diario por URL (lento)", value=False, key="daily_ev")

    return lag_days, pais, seccion, incluir_diario, start_date, end_date

# ----------------- Parámetros: Auditoría -----------------

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

# ----------------- Parámetros: Nombres (KG + Wikipedia) -----------------

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

    items_from_csv: list[dict] = []
    names_from_text: list[dict] = []

    csv_col = None
    loc_col = None

    if df is not None:
        cols = list(df.columns)
        # Columna probable
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
                for _, row in df.iterrows():
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
    merged_items: list[dict] = []
    for src in (items_from_csv, names_from_text):
        for it in src:
            key = (it["name"], it.get("row_location", ""))
            if (not dedup) or key not in seen:
                if dedup:
                    seen.add(key)
                merged_items.append(it)

    st.caption(f"Total de filas a analizar: **{len(merged_items)}**")

    c1, c2, c3 = st.columns([1,1,1])
    with c1:
        lang = st.selectbox("Idioma (para KG/Wiki)", ["es","en","pt","fr","it","de"], index=0, key="names_lang")
    with c2:
        match_mode = st.selectbox("Modo de coincidencia", ["Flexible (recomendado)", "Estricto"], index=0, key="names_match_mode")
    with c3:
        strategy = st.selectbox("Estrategia", ["Balance (KG + Wikipedia)"], index=0, key="names_strategy")

    entity_type_label = st.selectbox(
        "Tipo de entidad (global)",
        ["Inferir automáticamente", "Persona", "Organización", "Lugar", "Evento", "Obra creativa", "Producto", "Otro"],
        index=0, key="names_entity_type"
    )

    global_terms = st.text_input(
        "Términos complementarios (globales, opcional)",
        value="", placeholder="ej.: periodista argentino, club de fútbol, banda de rock",
        key="names_global_terms"
    )

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

# ----------------- Parámetros: Discover Snoop (CSV externo, sin GSC) -----------------

def params_for_discover_snoop() -> dict:
    """
    Construye parámetros para el análisis 'Discover Snoop'.
    No depende del CSV (que se carga en app.py) para no romper el layout.
    """
    st.markdown("#### Parámetros (Discover Snoop)")
    st.caption(
        "Subí el CSV en la pantalla principal y usá estas opciones para filtrar, deduplicar y detectar señales."
    )

    # --- Ventana temporal ---
    lag_days = st.number_input("Lag de datos (evitar días incompletos)", 0, 10, LAG_DAYS_DEFAULT, key="ds_lag")
    end_default = date.today() - timedelta(days=lag_days)

    modo_periodo = st.selectbox(
        "Período a considerar",
        ["Últimos 28 días", "Últimos 60 días", "Últimos 90 días", "Rango personalizado"],
        index=0, key="ds_period_mode"
    )

    start_date = None
    end_date = None
    if modo_periodo == "Rango personalizado":
        c1, c2 = st.columns(2)
        with c1:
            start_date = st.date_input("Desde (YYYY-MM-DD)", key="ds_start")  # puede quedar None
        with c2:
            end_date = st.date_input("Hasta (YYYY-MM-DD)", value=end_default, key="ds_end")
    else:
        days = 28 if "28" in modo_periodo else (60 if "60" in modo_periodo else 90)
        end_date = end_default
        start_date = end_date - timedelta(days=days - 1)
        st.caption(f"Ventana estimada: **{start_date} → {end_date}**")

    # --- Filtros por publisher / categoría / entidades ---
    st.markdown("##### Filtros (listas separadas por coma)")
    pub_inc = st.text_input("Incluir solo publishers (opcional)", value="", key="ds_pub_inc", placeholder="ej.: Clarín, La Nación")
    pub_exc = st.text_input("Excluir publishers (opcional)", value="", key="ds_pub_exc", placeholder="ej.: Sitio X, Sitio Y")

    cat_inc = st.text_input("Incluir solo categorías (opcional)", value="", key="ds_cat_inc", placeholder="ej.: Deportes, Espectáculos")
    cat_exc = st.text_input("Excluir categorías (opcional)", value="", key="ds_cat_exc", placeholder="ej.: Política")

    ent_inc = st.text_input("Incluir solo entidades (opcional)", value="", key="ds_ent_inc", placeholder="ej.: Lionel Messi, iPhone 16")
    ent_exc = st.text_input("Excluir entidades (opcional)", value="", key="ds_ent_exc", placeholder="ej.: TikTok")

    # --- Filtro por secciones (paths) + subsecciones (como en Core) ---
    st.markdown("##### Filtro por secciones (paths)")
    sec_mode = st.radio("¿Cómo aplicar el filtro de sección?", ["No filtrar", "Incluir solo", "Excluir"], index=0, horizontal=True, key="ds_sec_mode")
    sec_list_txt = st.text_input("Secciones (ej.: /deportes/, /espectaculos/)", value="", key="ds_sec_list", placeholder="/deportes/, /espectaculos/")

    st.markdown("##### Filtro por subsecciones (opcional)")
    sub_enabled = st.checkbox("Activar filtro por subsecciones", value=False, key="ds_sub_en")
    sub_mode = None
    sub_list_txt = None
    if sub_enabled:
        sub_mode = st.radio("Modo de subsecciones", ["Incluir solo", "Excluir"], index=0, horizontal=True, key="ds_sub_mode")
        sub_list_txt = st.text_input("Subsecciones (ej.: /deportes/futbol/)", value="", key="ds_sub_list", placeholder="/deportes/futbol/")

    sec_paths = _parse_paths_csv(sec_list_txt)
    sub_paths = _parse_paths_csv(sub_list_txt) if sub_list_txt is not None else None
    adv_payload = _build_advanced_filters_payload(sec_mode, sec_paths, sub_enabled, sub_mode, sub_paths)

    # --- Deduplicación / normalización de URLs ---
    st.markdown("##### Normalización y deduplicación")
    dedupe_mode = st.selectbox(
        "Modo de deduplicación",
        ["Por URL exacta", "Por ruta sin querystring", "Por slug + título (agresivo)"],
        index=1, key="ds_dedupe"
    )
    strip_query = st.checkbox("Quitar parámetros de tracking (utm, gclid, etc.)", value=True, key="ds_strip_qs")
    normalize_trailing = st.checkbox("Normalizar / y mayúsculas/minúsculas en path", value=True, key="ds_norm_trailing")

    # --- Umbrales y señales ---
    st.markdown("##### Umbrales")
    min_views = st.number_input("Mínimo de apariciones (views) para considerar una URL", 1, 1000, 2, key="ds_min_views")
    min_days_visible = st.number_input("Mínimo de días vista en la ventana", 1, 90, 1, key="ds_min_days")
    top_n_entities = st.number_input("Top entidades a destacar", 5, 100, 20, key="ds_topn_entities")
    top_n_categories = st.number_input("Top categorías a destacar", 5, 100, 20, key="ds_topn_categories")

    st.markdown("##### Alertas")
    st.caption("Marcamos picos/caídas comparando tramos internos en la ventana y contenido viejo.")
    spike_mult = st.number_input("Umbral de pico (x veces sobre su mediana)", 1.0, 10.0, 2.5, step=0.1, key="ds_spike_mult")
    drop_mult = st.number_input("Umbral de caída (dividido por x respecto a su mediana)", 1.0, 10.0, 2.5, step=0.1, key="ds_drop_mult")
    stale_days = st.number_input("Staleness: días sin verse para marcar contenido viejo", 7, 180, 30, key="ds_stale_days")

    include_raw = st.checkbox("Incluir pestaña de datos crudos (RAW)", value=False, key="ds_include_raw")

    # --- Ensamblar parámetros ---
    return {
        "lag_days": int(lag_days),
        "period": {
            "mode": "custom" if modo_periodo == "Rango personalizado" else "last",
            "start_date": start_date,
            "end_date": end_date,
            "days": None if modo_periodo == "Rango personalizado" else (28 if "28" in modo_periodo else (60 if "60" in modo_periodo else 90)),
        },
        "filters": {
            "publishers": {
                "include": _parse_paths_csv(pub_inc),
                "exclude": _parse_paths_csv(pub_exc),
            },
            "categories": {
                "include": _parse_paths_csv(cat_inc),
                "exclude": _parse_paths_csv(cat_exc),
            },
            "entities": {
                "include": _parse_paths_csv(ent_inc),
                "exclude": _parse_paths_csv(ent_exc),
            },
            "sections_payload": adv_payload,  # mismo formato que core/evergreen
        },
        "dedupe": {
            "mode": ("url" if dedupe_mode.startswith("Por URL")
                     else "path" if "ruta" in dedupe_mode
                     else "slug_title"),
            "strip_query": bool(strip_query),
            "normalize_trailing": bool(normalize_trailing),
        },
        "thresholds": {
            "min_views": int(min_views),
            "min_days_visible": int(min_days_visible),
        },
        "highlights": {
            "top_n_entities": int(top_n_entities),
            "top_n_categories": int(top_n_categories),
        },
        "alerts": {
            "spike_multiplier": float(spike_mult),
            "drop_multiplier": float(drop_mult),
            "stale_days": int(stale_days),
        },
        "output": {
            "include_raw": bool(include_raw),
        },
    }