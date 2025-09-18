# seo_analisis_ext/discover_retention.py
from __future__ import annotations

"""
Discover Retention (Incorporación y permanencia en Discover, por hora)

- Consulta la API de Search Console (Search Analytics) para Discover
  * Totales por URL en el período (clics/impresiones)
  * Series por HORA (requiere dataState='HOURLY_ALL' + dimension 'HOUR')
  * Soporta chunking en ventanas de ≤10 días cuando se piden horas

- Construye un Google Sheet a partir de un template dado:
  * Nombre:  "<dominio> - <nombre del análisis> - <fecha de ejecución>"
  * Hoja 'Configuración' con la info del análisis
  * Hoja 'Análisis' con columnas:
        URL,
        Clics del período,
        Impresiones del período,
        Sección,
        Fecha de publicación,
        Hora de publicación,
        Fecha de ingreso a Discover,
        Hora de ingreso a Discover,
        Días de permanencia,
        Última visualización en Discover,
        Status

Status:
  - "Contenido publicado previo al análisis"      si pub_date < start
  - "Contenido dentro del período de análisis"    si last_seen < end
  - "Contenido aún vigente"                       si last_seen >= end
  - "Revisar"                                     si faltan datos clave

Notas:
  - Si la propiedad aún no admite HOUR+page, se intenta HOUR solamente.
    En ese caso no se podrá computar por-URL (se devuelve vacío).
  - Para la FECHA/HORA de publicación se hace scraping liviano (opcional).
"""

from dataclasses import dataclass
from typing import Any, Iterable, Optional, Tuple, Dict, List
from datetime import date, datetime, timedelta
from urllib.parse import urlsplit

import re

try:
    import pandas as pd  # type: ignore
except Exception:  # pragma: no cover
    pd = None  # type: ignore

# =============================================================================
# Parámetros (clase flexible que ignora claves extra como 'window')
# =============================================================================

class DiscoverRetentionParams:
    """
    Clase simple (no dataclass) para tolerar kwargs desconocidos (ej. 'window').

    Args (todos opcionales excepto start/end):
        start (str|date): 'YYYY-MM-DD'
        end   (str|date): 'YYYY-MM-DD'
        section (str|None): patrón de sección (p.ej. "/vida/")
        country (str|None): ISO (ej. 'arg', 'esp') — se envía en lower-case
        template_id (str): ID del template de Sheets
        analysis_name (str): nombre visible del análisis
        scrape_publication (bool): si debe scrappear fecha/hora de publicación
        scrape_concurrency (int): concurrencia de scraping
        scrape_timeout_s (int): timeout por request
    """
    def __init__(
        self,
        *,
        start: Any = None,
        end: Any = None,
        section: Optional[str] = None,
        country: Optional[str] = None,
        template_id: Optional[str] = None,
        analysis_name: Optional[str] = None,
        scrape_publication: bool = False,
        scrape_concurrency: int = 8,
        scrape_timeout_s: int = 7,
        **_ignore: Any,  # <= tolera 'window' u otros extras
    ) -> None:
        self.start = start
        self.end = end
        self.section = section
        self.country = country
        self.template_id = template_id or "1SB9wFHWyDfd5P-24VBP7-dE1f1t7YvVYjnsc2XjqU8M"
        self.analysis_name = analysis_name or "Discover - Incorporación y permanencia"
        self.scrape_publication = bool(scrape_publication)
        self.scrape_concurrency = int(scrape_concurrency)
        self.scrape_timeout_s = int(scrape_timeout_s)

    def to_dict(self) -> dict:
        return {
            "start": self.start,
            "end": self.end,
            "section": self.section,
            "country": self.country,
            "template_id": self.template_id,
            "analysis_name": self.analysis_name,
            "scrape_publication": self.scrape_publication,
            "scrape_concurrency": self.scrape_concurrency,
            "scrape_timeout_s": self.scrape_timeout_s,
        }


# =============================================================================
# Helpers
# =============================================================================

def _as_date(d: Any) -> date:
    if isinstance(d, date):
        return d
    s = str(d).strip()
    return date(int(s[0:4]), int(s[5:7]), int(s[8:10]))

def _iso_date(d: Any) -> str:
    try:
        return d.isoformat()  # type: ignore[attr-defined]
    except Exception:
        return str(d)

def _domain_from_site(site_url: str) -> str:
    try:
        u = site_url.strip()
        if not u:
            return "sitio"
        if "://" not in u:
            u = "https://" + u
        net = urlsplit(u).netloc
        return net or u.strip("/").replace("https://", "").replace("http://", "")
    except Exception:
        return site_url.replace("https://", "").replace("http://", "").strip("/")

def _first_section(url: str) -> str:
    try:
        # Quita esquema + dominio y toma primer segmento de path
        path = url.split("://", 1)[-1].split("/", 1)[-1]
        seg = path.split("/", 1)[0]
        return (seg or "").strip()
    except Exception:
        return ""

def _iter_date_chunks(start: date, end: date, max_days: int = 10) -> Iterable[Tuple[date, date]]:
    cur = start
    while cur <= end:
        stop = min(end, cur + timedelta(days=max_days - 1))
        yield cur, stop
        cur = stop + timedelta(days=1)

def _parse_hour_key(iso_with_tz: str) -> datetime:
    # Ej. "2025-04-07T01:00:00-07:00"
    return datetime.fromisoformat(str(iso_with_tz))

def _build_filters(section: Optional[str], country: Optional[str]) -> List[dict]:
    filters: List[dict] = []
    if section:
        expr = str(section).strip()
        if expr and not expr.startswith("/"):
            expr = "/" + expr
        filters.append({"dimension": "page", "operator": "contains", "expression": expr})
    if country:
        filters.append({"dimension": "country", "operator": "equals", "expression": str(country).strip().lower()})
    return filters

# =============================================================================
# Google Sheets I/O (simple, compatible con el parche de app_ext)
# =============================================================================

def _ensure_worksheet(sh, title: str):
    try:
        return sh.worksheet(title)
    except Exception:
        return sh.add_worksheet(title=title, rows=200, cols=30)

def _coerce_df_values(df: pd.DataFrame) -> List[List[Any]]:
    if df is None or df.empty:
        return [["(sin datos)"]]
    return [list(df.columns)] + df.fillna("").astype(str).values.tolist()

def _write_ws(gs_client, spreadsheet_or_id, title: str, df_or_values: Any):
    """
    Función que app_ext puede parchear para serialización segura.
    Acepta:
      - df_or_values: DataFrame o lista de listas
    """
    sh = spreadsheet_or_id
    if not hasattr(sh, "id"):  # si viene ID, abrir
        sh = gs_client.open_by_key(str(spreadsheet_or_id))
    ws = _ensure_worksheet(sh, title)

    if pd is not None and isinstance(df_or_values, pd.DataFrame):
        values = _coerce_df_values(df_or_values)
    else:
        values = df_or_values

    try:
        ws.clear()
    except Exception:
        pass
    ws.update(values)

# =============================================================================
# GSC Queries
# =============================================================================

def _gsc_query_discover_pages(sc, site_url: str, start: date, end: date,
                              *, filters: Optional[List[dict]] = None,
                              row_limit: int = 25000) -> pd.DataFrame:
    body = {
        "startDate": _iso_date(start),
        "endDate": _iso_date(end),
        "dimensions": ["page"],
        "rowLimit": int(row_limit),
        "startRow": 0,
        "type": "discover",
    }
    if filters:
        body["dimensionFilterGroups"] = [{"groupType": "and", "filters": filters}]
    resp = sc.searchanalytics().query(siteUrl=site_url, body=body).execute()
    rows = resp.get("rows", []) or []
    out = []
    for r in rows:
        keys = r.get("keys") or []
        page = keys[0] if keys else ""
        out.append({
            "page": page,
            "clicks": r.get("clicks", 0),
            "impressions": r.get("impressions", 0),
        })
    return pd.DataFrame(out, columns=["page", "clicks", "impressions"])

def _gsc_query_discover_hourly_chunk(sc, site_url: str, start: date, end: date,
                                     *, filters: Optional[List[dict]] = None,
                                     row_limit: int = 25000) -> pd.DataFrame:
    """
    Intenta HOUR+page; si bota error, reintenta HOUR solo.
    Devuelve columnas: ts (datetime), page (str|None), clicks, impressions
    """
    body = {
        "startDate": _iso_date(start),
        "endDate": _iso_date(end),
        "dataState": "HOURLY_ALL",      # requerido al agrupar por HORA
        "dimensions": ["HOUR", "page"], # clave: 'HOUR' en mayúscula
        "rowLimit": int(row_limit),
        "startRow": 0,
        "type": "discover",
    }
    if filters:
        body["dimensionFilterGroups"] = [{"groupType": "and", "filters": filters}]

    try:
        resp = sc.searchanalytics().query(siteUrl=site_url, body=body).execute()
    except Exception:
        body["dimensions"] = ["HOUR"]
        resp = sc.searchanalytics().query(siteUrl=site_url, body=body).execute()

    rows = resp.get("rows", []) or []
    out = []
    for r in rows:
        keys = r.get("keys") or []
        ts = _parse_hour_key(keys[0]) if keys else None
        page = keys[1] if len(keys) > 1 else None
        out.append({
            "ts": ts,
            "page": page,
            "clicks": r.get("clicks", 0),
            "impressions": r.get("impressions", 0),
        })
    return pd.DataFrame(out, columns=["ts", "page", "clicks", "impressions"])

def _gsc_query_discover_hourly(sc, site_url: str, start: date, end: date,
                               *, filters: Optional[List[dict]] = None) -> pd.DataFrame:
    """
    Junta varios chunks (≤10 días) para cubrir el período completo.
    """
    frames = []
    for s, e in _iter_date_chunks(start, end, max_days=10):
        try:
            df = _gsc_query_discover_hourly_chunk(sc, site_url, s, e, filters=filters)
            if df is not None and not df.empty:
                frames.append(df)
        except Exception:
            # Si un chunk falla, continuamos para no abortar todo el análisis
            continue
    if not frames:
        return pd.DataFrame(columns=["ts", "page", "clicks", "impressions"])
    return pd.concat(frames, ignore_index=True)

# =============================================================================
# Scraping (opcional) de fecha/hora de publicación
# =============================================================================

_META_TIME_KEYS = [
    # OpenGraph / article
    ('meta', 'article:published_time'),
    ('meta', 'og:published_time'),
    # Otros comunes
    ('meta', 'publication_date'),
    ('meta', 'pubdate'),
]

_JSONLD_PATTERNS = [
    r'"datePublished"\s*:\s*"([^"]+)"',
    r'"uploadDate"\s*:\s*"([^"]+)"',
    r'"dateCreated"\s*:\s*"([^"]+)"',
]

def _parse_dt_guess(s: str) -> Optional[datetime]:
    s = (s or "").strip()
    if not s:
        return None
    # ISO directo
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        pass
    # Fecha + hora parcial
    m = re.search(r'(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}(?::\d{2})?)', s)
    if m:
        iso = m.group(1) + "T" + m.group(2)
        try:
            return datetime.fromisoformat(iso)
        except Exception:
            pass
    # Solo fecha
    m = re.search(r'(\d{4}-\d{2}-\d{2})', s)
    if m:
        try:
            return datetime.fromisoformat(m.group(1) + "T00:00:00")
        except Exception:
            pass
    return None

def _extract_pub_from_html(text: str) -> Optional[datetime]:
    # Busca meta tags
    for tag, key in _META_TIME_KEYS:
        pattern = rf'<{tag}[^>]+(?:property|name)\s*=\s*["\']{re.escape(key)}["\'][^>]*content\s*=\s*["\']([^"\']+)["\']'
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            dt = _parse_dt_guess(m.group(1))
            if dt:
                return dt
    # Busca JSON-LD simple
    for pat in _JSONLD_PATTERNS:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            dt = _parse_dt_guess(m.group(1))
            if dt:
                return dt
    return None

def _fetch_publication_map(urls: List[str], *, concurrency: int = 8, timeout_s: int = 7) -> Dict[str, Tuple[Optional[str], Optional[str]]]:
    """
    Devuelve { url: (fecha_publicación_str, hora_publicación_str) }
    Si falla, retorna valores vacíos.
    """
    out: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
    try:
        import concurrent.futures as cf
        import requests  # type: ignore
    except Exception:
        return {u: ("", "") for u in urls}

    def _one(u: str) -> Tuple[str, str]:
        try:
            r = requests.get(u, timeout=timeout_s, headers={"User-Agent": "Mozilla/5.0 (compatible; RetentionBot/1.0)"})
            if r.status_code >= 400:
                return ("", "")
            dt = _extract_pub_from_html(r.text)
            if not dt:
                return ("", "")
            return (dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M"))
        except Exception:
            return ("", "")

    with cf.ThreadPoolExecutor(max_workers=max(1, int(concurrency))) as ex:
        for u, res in zip(urls, ex.map(_one, urls)):
            out[u] = res
    return out

# =============================================================================
# Runner principal
# =============================================================================

def run_discover_retention(
    sc_service,
    drive_service,
    gs_client,
    site_url: str,
    params: Dict[str, Any] | DiscoverRetentionParams,
    dest_folder_id: Optional[str] = None,
) -> Optional[str]:
    """
    Ejecuta el análisis y devuelve el Sheet ID (o None si falla).
    """
    if pd is None:
        raise RuntimeError("Pandas es requerido para 'discover_retention'.")

    # --- Normalizar parámetros
    if isinstance(params, DiscoverRetentionParams):
        p = params.to_dict()
    else:
        p = dict(params or {})
        # tolera payloads con 'window'
        win = dict(p.get("window") or {})
        start = p.get("start") or p.get("start_date") or win.get("start") or win.get("start_date")
        end   = p.get("end")   or p.get("end_date")   or win.get("end")   or win.get("end_date")
        p.setdefault("start", start)
        p.setdefault("end", end)
        p.setdefault("template_id", p.get("template_id") or "1SB9wFHWyDfd5P-24VBP7-dE1f1t7YvVYjnsc2XjqU8M")
        p.setdefault("analysis_name", p.get("analysis_name") or "Discover - Incorporación y permanencia")
        p.setdefault("scrape_publication", bool(p.get("scrape_publication", False)))
        p.setdefault("scrape_concurrency", int(p.get("scrape_concurrency", 8)))
        p.setdefault("scrape_timeout_s", int(p.get("scrape_timeout_s", 7)))

    try:
        start_d = _as_date(p["start"])
        end_d   = _as_date(p["end"])
    except Exception:
        raise RuntimeError("Parámetros inválidos: start/end requeridos (YYYY-MM-DD).")

    section = p.get("section")
    country = p.get("country")
    template_id = p["template_id"]
    analysis_name = p["analysis_name"]
    scrape_publication = bool(p.get("scrape_publication"))
    scrape_concurrency = int(p.get("scrape_concurrency", 8))
    scrape_timeout_s   = int(p.get("scrape_timeout_s", 7))

    # --- Preparar filtros para la API
    filters = _build_filters(section, country)

    # --- 1) Totales por página en el período
    try:
        df_period = _gsc_query_discover_pages(sc_service, site_url, start_d, end_d, filters=filters)
    except Exception as e:
        # Si falla la API, devolvemos None para que la app muestre feedback
        raise RuntimeError(f"Fallo al consultar páginas del período: {e}")

    # --- 2) Horario (en chunks ≤10 días) para ingreso/última visualización
    df_hourly = _gsc_query_discover_hourly(sc_service, site_url, start_d, end_d, filters=filters)

    # Si la propiedad no admite HOUR+page y tampoco hubo fallback con 'page', no podremos calcular
    if df_hourly is None or df_hourly.empty or ("page" not in df_hourly.columns):
        # Seguimos igual, pero sin primeras/últimas — quedarán en blanco
        df_hourly = pd.DataFrame(columns=["ts", "page", "clicks", "impressions"])

    # --- 3) Primer/último timestamp por URL
    # Filtramos por impresiones > 0 para evitar horas "vacías" (en general no aparecen, pero por las dudas)
    dfh = df_hourly.loc[(df_hourly.get("impressions", 0) > 0)]
    grp = dfh.groupby("page", dropna=True) if "page" in dfh.columns else None

    if grp is not None and hasattr(grp, "size"):
        first_ts = grp["ts"].min()
        last_ts  = grp["ts"].max()
        df_status = pd.DataFrame({
            "URL": first_ts.index,
            "Fecha de ingreso a Discover": first_ts.dt.date.values,
            "Hora de ingreso a Discover": first_ts.dt.strftime("%H:%M").values,
            "Última visualización en Discover": last_ts.dt.strftime("%Y-%m-%d %H:%M").values,
        }).reset_index(drop=True)
        df_status["Días de permanencia"] = (last_ts.dt.date - first_ts.dt.date).dt.days.values
    else:
        df_status = pd.DataFrame(columns=[
            "URL",
            "Fecha de ingreso a Discover",
            "Hora de ingreso a Discover",
            "Última visualización en Discover",
            "Días de permanencia",
        ])

    # --- 4) Armar base de salida
    df_out = (
        df_status.merge(df_period, how="right", left_on="URL", right_on="page")
                 .drop(columns=["page"])
                 .rename(columns={"clicks": "Clics del período",
                                  "impressions": "Impresiones del período"})
    )
    if "URL" not in df_out.columns:
        df_out["URL"] = df_period["page"] if "page" in df_period.columns else ""

    # Sección
    df_out["Sección"] = df_out["URL"].astype(str).map(_first_section)

    # --- 5) Publicación (opcional)
    df_out["Fecha de publicación"] = ""
    df_out["Hora de publicación"] = ""
    if scrape_publication and not df_out.empty:
        urls = df_out["URL"].dropna().astype(str).tolist()
        pub_map = _fetch_publication_map(urls, concurrency=scrape_concurrency, timeout_s=scrape_timeout_s)
        df_out["Fecha de publicación"] = df_out["URL"].map(lambda u: (pub_map.get(u) or ("",""))[0])
        df_out["Hora de publicación"]  = df_out["URL"].map(lambda u: (pub_map.get(u) or ("",""))[1])

    # --- 6) Status
    def _compute_status(row, start_date: date, end_date: date) -> str:
        pub_date = str(row.get("Fecha de publicación") or "").strip()
        last_str = str(row.get("Última visualización en Discover") or "").strip()

        s = start_date
        e = end_date

        pub_ok = False
        if pub_date:
            try:
                pub_d = _as_date(pub_date)
                pub_ok = True
            except Exception:
                pub_ok = False

        last_ok = False
        if last_str:
            try:
                last_dt = datetime.fromisoformat(last_str.replace(" ", "T"))
                last_d = last_dt.date()
                last_ok = True
            except Exception:
                last_ok = False

        if pub_ok and pub_d < s:
            return "Contenido publicado previo al análisis"
        if last_ok and last_d < e:
            return "Contenido dentro del período de análisis"
        if last_ok and last_d >= e:
            return "Contenido aún vigente"
        return "Revisar"

    df_out["Status"] = df_out.apply(_compute_status, axis=1, args=(start_d, end_d))

    # --- 7) Reordenar columnas exactamente como solicita el brief
    ordered_cols = [
        "URL",
        "Clics del período",
        "Impresiones del período",
        "Sección",
        "Fecha de publicación",
        "Hora de publicación",
        "Fecha de ingreso a Discover",
        "Hora de ingreso a Discover",
        "Días de permanencia",
        "Última visualización en Discover",
        "Status",
    ]
    for c in ordered_cols:
        if c not in df_out.columns:
            df_out[c] = ""
    df_out = df_out[ordered_cols]
    # Orden sugerido: por Clics del período desc
    try:
        df_out = df_out.sort_values("Clics del período", ascending=False)
    except Exception:
        pass

    # --- 8) Crear Sheet desde TEMPLATE y escribir
    site_pretty = _domain_from_site(site_url)
    today_str = date.today().isoformat()
    new_name = f"{site_pretty} - {analysis_name} - {today_str}"

    # Copiar de template
    body = {"name": new_name}
    if dest_folder_id:
        body["parents"] = [dest_folder_id]

    try:
        copied = drive_service.files().copy(fileId=template_id, body=body, fields="id,name").execute()
        sid = copied["id"]
    except Exception as e:
        raise RuntimeError(f"No se pudo copiar el template: {e}")

    # Abrir y escribir
    try:
        sh = gs_client.open_by_key(sid)
    except Exception as e:
        raise RuntimeError(f"No se pudo abrir el Sheet copiado: {e}")

    # Configuración
    cfg_rows = [["Configuración", "Valores"]]
    cfg_rows += [
        ["Sitio Analizado", site_pretty],
        ["Tipo de análisis", analysis_name],
        ["Periodo analizado", f"{_iso_date(start_d)} a {_iso_date(end_d)}"],
    ]
    if section:
        cfg_rows.append(["Sección", str(section)])
    if country:
        cfg_rows.append(["País", str(country).upper()])

    _write_ws(gs_client, sh, "Configuración", cfg_rows)

    # Análisis
    _write_ws(gs_client, sh, "Análisis", df_out)

    # (Opcional) Meta/Debug
    try:
        meta_df = pd.DataFrame({
            "campo": ["site_url", "start", "end", "section", "country"],
            "valor": [site_url, _iso_date(start_d), _iso_date(end_d), section or "(todas)", str(country).upper() if country else "(Global)"],
        })
        _write_ws(gs_client, sh, "Meta", meta_df)
    except Exception:
        pass

    return sid
