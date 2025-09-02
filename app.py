# app.py
# -*- coding: utf-8 -*-
"""
Analizador SEO - Extracción GSC + Scraping + Excel
--------------------------------------------------
- Lee un config JSON (archivo o stdin)
- Obtiene datos de GSC (si está disponible y source lo indica)
- Scrapea metadatos de URLs (si source lo indica)
- Aplica filtros, mergea y exporta a Excel con nombre y fechas correctas
- Escribe hoja 'debug' con métricas y pasos, para ubicar dónde se vacía el flujo

Uso:
    python app.py --config config.json
    # o por stdin:
    cat config.json | python app.py --stdin

Auth GSC:
    - Variable de entorno GSC_SITE (propiedad verificada, ej: "https://radiomitre.cienradios.com/")
    - Service account JSON: GOOGLE_APPLICATION_CREDENTIALS=/ruta/cred.json
      (La cuenta de servicio debe estar agregada como propietario/usuario en Search Console)
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from typing import Dict, List, Tuple, Any, Optional

# ---- Dependencias no críticas (manejamos ausencia) ----
try:
    import pandas as pd
except Exception as e:
    print(f"[FATAL] Falta pandas: {e}", file=sys.stderr)
    sys.exit(1)

try:
    import requests
except Exception as e:
    print(f"[FATAL] Falta requests: {e}", file=sys.stderr)
    sys.exit(1)

try:
    from bs4 import BeautifulSoup
except Exception as e:
    print(f"[FATAL] Falta beautifulsoup4: {e}", file=sys.stderr)
    sys.exit(1)

# GSC opcional
HAVE_GOOGLE = True
try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
except Exception:
    HAVE_GOOGLE = False


# ---------------- Utilidades ---------------- #

def today_utc_date() -> dt.date:
    return dt.datetime.utcnow().date()


def parse_bool(x: Any, default=False) -> bool:
    if isinstance(x, bool):
        return x
    if isinstance(x, str):
        return x.strip().lower() in ("1", "true", "t", "yes", "y", "si", "sí")
    return default


def safe_int(x: Any, default: int = 0) -> int:
    try:
        return int(x)
    except Exception:
        return default


def normalize_url_list(cfg: Dict) -> List[str]:
    """Unifica seeds desde múltiples campos: seeds (obj), seed_urls (list), pages (list)."""
    urls = []

    # seeds: [{"url": "..."}]
    seeds = cfg.get("seeds") or []
    for it in seeds:
        if isinstance(it, dict) and "url" in it and isinstance(it["url"], str):
            urls.append(it["url"])

    # seed_urls: ["..."]
    seed_urls = cfg.get("seed_urls") or []
    for u in seed_urls:
        if isinstance(u, str):
            urls.append(u)

    # pages: ["..."]
    pages = cfg.get("pages") or []
    for u in pages:
        if isinstance(u, str):
            urls.append(u)

    # De-duplicar preservando orden
    seen = set()
    uniq = []
    for u in urls:
        if u not in seen:
            uniq.append(u)
            seen.add(u)
    return uniq


def filter_article_like(urls: List[str]) -> List[str]:
    """
    Filtro heurístico para descartar players/home/tags/etc.
    Ajustado a patrones vistos en radiomitre.
    """
    bad_patterns = [
        r"/player", r"/tag/", r"/hd/", r"/programacion", r"/ultimas-noticias/?$",
        r"/$", r"/mitre-\w+/$", r"/nuestro-aire/", r"/buen-vivir/$"
    ]
    rx = re.compile("|".join(bad_patterns))
    return [u for u in urls if not rx.search(u)]


def compute_window(window_cfg: Dict, lag_days: int) -> Tuple[str, str]:
    """
    Devuelve (start_date, end_date) en formato YYYY-MM-DD.
    Respeta window.mode: "custom" o "last"
    """
    mode = (window_cfg or {}).get("mode") or "last"
    today = today_utc_date()
    lag = dt.timedelta(days=safe_int(lag_days, 0))

    if mode == "custom":
        s = window_cfg.get("start_date")
        e = window_cfg.get("end_date")
        if not s or not e:
            raise ValueError("En 'custom', start_date y end_date son obligatorios.")
        # Ajuste por lag: recortamos end si corresponde
        end = min(dt.date.fromisoformat(e), today - lag) if lag.days > 0 else dt.date.fromisoformat(e)
        start = dt.date.fromisoformat(s)
    else:
        # mode == "last"
        ndays = safe_int(window_cfg.get("days"), 7)
        end = today - lag
        start = end - dt.timedelta(days=max(ndays - 1, 0))

    return (start.isoformat(), end.isoformat())


def date_span_for_filename(start: str, end: str) -> str:
    """Para nombre de archivo: 2025-08-03_a_2025-08-30"""
    try:
        _ = dt.date.fromisoformat(start)
        _ = dt.date.fromisoformat(end)
        return f"{start}_a_{end}"
    except Exception:
        # fallback para que nunca salga None
        return f"{start or 'inicio'}_a_{end or 'fin'}"


def domain_from_urls(urls: List[str]) -> str:
    for u in urls:
        m = re.match(r"https?://([^/]+)/?", u)
        if m:
            return m.group(1)
    return "sitio"


def default_headers(ua: Optional[str]) -> Dict[str, str]:
    ua = ua or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    return {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Connection": "close",
    }


# ---------------- GSC ---------------- #

def fetch_gsc_rows(
    site_url: str,
    start_date: str,
    end_date: str,
    cfg: Dict,
    limit: int = 5000
) -> pd.DataFrame:
    """
    Trae filas de Search Analytics agrupadas por 'page'.
    Requiere variables/credenciales válidas; si falla, devuelve DF vacío.
    Aplica filtros básicos (country/device) si están presentes en el config.
    """
    if not HAVE_GOOGLE:
        print("[WARN] Librerías de Google no instaladas. Saltando GSC.")
        return pd.DataFrame()

    if not site_url:
        print("[WARN] GSC_SITE no definido. Saltando GSC.")
        return pd.DataFrame()

    try:
        creds = None
        # Usa GOOGLE_APPLICATION_CREDENTIALS si existe
        gpath = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        if gpath and os.path.exists(gpath):
            creds = service_account.Credentials.from_service_account_file(gpath, scopes=["https://www.googleapis.com/auth/webmasters.readonly"])
        else:
            # Permitimos ADC (Application Default Credentials) si estuviera configurado
            try:
                import google.auth
                creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/webmasters.readonly"])
            except Exception as e:
                print(f"[WARN] No se encontraron credenciales GSC válidas: {e}. Saltando GSC.")
                return pd.DataFrame()

        svc = build("webmasters", "v3", credentials=creds, cache_discovery=False)

        # searchType a partir de cfg['tipo'] (fallback web)
        tipo = (cfg.get("tipo") or "web").lower()
        if tipo in ("ambos", "both", "all", "todo", "news"):
            # La API no soporta "ambos"; usamos "web" por defecto
            tipo = "web"

        body: Dict[str, Any] = {
            "startDate": start_date,
            "endDate": end_date,
            "dimensions": ["page"],
            "searchType": tipo,
            "rowLimit": min(int(cfg.get("max_urls") or 5000), limit),
        }

        # Filtros opcionales
        country = (cfg.get("filters") or {}).get("country")
        device = (cfg.get("filters") or {}).get("device")

        if country or device:
            dfs = []
            if country:
                dfs.append({
                    "dimension": "country",
                    "operator": "equals",
                    "expression": country
                })
            if device:
                dfs.append({
                    "dimension": "device",
                    "operator": "equals",
                    "expression": device
                })
            body["dimensionFilterGroups"] = [{"filters": dfs}]

        # Ordenamiento si corresponde
        order_by = (cfg.get("order_by") or "clicks").lower()
        order_col_map = {
            "clicks": "clicks",
            "impressions": "impressions",
            "ctr": "ctr",
            "position": "position"
        }
        if order_by in order_col_map:
            body["orderBy"] = [{
                "field": order_col_map[order_by],
                "descending": True if order_by != "position" else False
            }]

        resp = svc.searchanalytics().query(siteUrl=site_url, body=body).execute()
        rows = resp.get("rows", [])
        if not rows:
            return pd.DataFrame(columns=["url", "clicks", "impressions", "ctr", "position"])

        data = []
        for r in rows:
            keys = r.get("keys") or []
            page = keys[0] if keys else None
            data.append({
                "url": page,
                "clicks": r.get("clicks", 0),
                "impressions": r.get("impressions", 0),
                "ctr": r.get("ctr", 0.0),
                "position": r.get("position", 0.0),
            })
        df = pd.DataFrame(data)

        # Filtros min
        filters = cfg.get("filters") or {}
        min_clicks = int(filters.get("min_clicks") or 0)
        min_impr = int(filters.get("min_impressions") or 0)
        if min_clicks > 0:
            df = df.loc[df["clicks"] >= min_clicks]
        if min_impr > 0:
            df = df.loc[df["impressions"] >= min_impr]

        # Limitar a max_urls final por si la API devolvió más
        max_urls = int(cfg.get("max_urls") or 300)
        df = df.sort_values(by=order_by if order_by in df.columns else "clicks", ascending=(order_by == "position")).head(max_urls).reset_index(drop=True)
        return df

    except HttpError as he:
        print(f"[ERROR] GSC HttpError: {he}", file=sys.stderr)
        return pd.DataFrame()
    except Exception as e:
        print(f"[ERROR] GSC error inesperado: {e}", file=sys.stderr)
        return pd.DataFrame()


# ---------------- Scraping ---------------- #

def extract_one(url: str, cfg: Dict, sess: requests.Session) -> Dict[str, Any]:
    """Scrapea una URL y devuelve un dict con campos pedidas en cfg['selectors'] o cfg['scrape']['selectors']."""
    sel = ((cfg.get("scrape") or {}).get("selectors")) or (cfg.get("selectors") or {})
    req = (cfg.get("scrape") or {}).get("request") or {}
    timeout = safe_int(req.get("timeout"), 12)
    ua = (req.get("user_agent") or "").strip() or None
    headers = default_headers(ua)

    out = {
        "url": url,
        "title": None,
        "headline": None,
        "subtitle": None,
        "author": None,
        "section": None,
        "tags": None,
        "published": None,
        "updated": None,
        "image": None,
        "content_text": None,
        "_status": "ok",
        "_error": None,
    }

    try:
        r = sess.get(url, headers=headers, timeout=timeout)
        r.raise_for_status()
        html = r.text

        soup = BeautifulSoup(html, "html.parser")

        def pick(sel_def: Dict[str, Any]) -> Optional[str]:
            if not sel_def:
                return None
            css = sel_def.get("css")
            attr = sel_def.get("attr")
            if not css:
                return None
            el = soup.select_one(css)
            if not el:
                return None
            if attr:
                return el.get(attr)
            return el.get_text(strip=True)

        # Campos según selectores
        out["title"] = pick(sel.get("title"))
        out["headline"] = pick(sel.get("headline"))
        out["subtitle"] = pick(sel.get("subtitle"))
        out["author"] = pick(sel.get("author"))
        out["section"] = pick(sel.get("section"))
        out["tags"] = pick(sel.get("tags"))
        out["published"] = pick(sel.get("published"))
        out["updated"] = pick(sel.get("updated"))
        out["image"] = pick(sel.get("image"))

        # Contenido
        content_sel = sel.get("content", {}).get("css") if sel.get("content") else None
        strip_sel = ((cfg.get("scrape") or {}).get("parse") or {}).get("strip_selectors")
        if content_sel:
            article = soup.select_one(content_sel) or soup
        else:
            article = soup
        # Strip elementos molestos
        if strip_sel:
            try:
                for bad in article.select(strip_sel):
                    bad.decompose()
            except Exception:
                pass

        # Texto principal
        out["content_text"] = article.get_text("\n", strip=True)[:10000] if article else None

        # Fallbacks
        if not out["title"]:
            t = soup.find("title")
            if t:
                out["title"] = t.get_text(strip=True)

        if not out["headline"]:
            h1 = soup.find("h1")
            if h1:
                out["headline"] = h1.get_text(strip=True)

        return out

    except Exception as e:
        out["_status"] = "error"
        out["_error"] = str(e)
        return out


def scrape_urls(urls: List[str], cfg: Dict) -> pd.DataFrame:
    """Scrapea en serie (robusto y simple). Si querés más velocidad, cambiar a ThreadPoolExecutor."""
    if not urls:
        return pd.DataFrame(columns=[
            "url", "title", "headline", "subtitle", "author", "section", "tags",
            "published", "updated", "image", "content_text", "_status", "_error"
        ])

    sess = requests.Session()
    sess.trust_env = False  # Ignora proxies del entorno

    rows = []
    for i, u in enumerate(urls, 1):
        row = extract_one(u, cfg, sess)
        rows.append(row)
        if i % 10 == 0:
            print(f"[SCRAPE] {i}/{len(urls)} URLs...")
    try:
        sess.close()
    except Exception:
        pass

    return pd.DataFrame(rows)


# ---------------- Pipeline principal ---------------- #

def run_pipeline(cfg: Dict) -> str:
    debug_lines: List[str] = []
    t0 = time.time()

    def dbg(msg: str):
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {msg}"
        debug_lines.append(line)
        print(line)

    # 1) Fechas
    lag_days = safe_int(cfg.get("lag_days"), 0)
    start_date, end_date = compute_window(cfg.get("window") or {}, lag_days)
    dbg(f"Ventana efectiva: start={start_date} end={end_date} (lag_days={lag_days})")

    # 2) Fuente(s)
    source = (cfg.get("source") or "both").lower().strip()
    if source not in ("gsc", "seeds", "both"):
        dbg(f"[WARN] source inválida '{source}', usando 'both'")
        source = "both"

    # 3) Unificar seeds
    seeds_all = normalize_url_list(cfg)
    seeds_filtered = filter_article_like(seeds_all)
    dbg(f"Seeds: antes={len(seeds_all)} después_filtro={len(seeds_filtered)}")

    # 4) GSC (si aplica)
    site_url = os.environ.get("GSC_SITE", "")
    df_gsc = pd.DataFrame()
    if source in ("gsc", "both"):
        df_gsc = fetch_gsc_rows(site_url, start_date, end_date, cfg, limit=10000)
        dbg(f"GSC: filas={len(df_gsc)} (site={site_url or 'N/D'})")

    # 5) Determinar URLs a scrappear
    max_urls = int(cfg.get("max_urls") or 300)
    urls_to_scrape: List[str] = []

    if source == "gsc":
        urls_to_scrape = df_gsc["url"].dropna().astype(str).tolist()
    elif source == "seeds":
        urls_to_scrape = seeds_filtered
    else:
        # both: union GSC + seeds
        set_urls = set(df_gsc["url"].dropna().astype(str).tolist()) if not df_gsc.empty else set()
        for u in seeds_filtered:
            set_urls.add(u)
        urls_to_scrape = list(set_urls)

    # Orden y límite
    urls_to_scrape = urls_to_scrape[:max_urls]
    dbg(f"URLs a scrape: {len(urls_to_scrape)} (limite={max_urls})")

    # 6) Preflight rápido (si no hay URLs -> ya sabemos donde muere)
    if len(urls_to_scrape) == 0:
        dbg("[STOP] No hay URLs para scrappear. Revisa filtros (`min_impressions`, `min_clicks`), `source`, `tipo`.")
        # Igual escribimos un Excel con debug para facilitar diagnóstico
        return write_excel_empty(cfg, start_date, end_date, debug_lines)

    # 7) Scraping
    df_scrape = scrape_urls(urls_to_scrape, cfg)
    dbg(f"Scrape: filas={len(df_scrape)} | ok={int((df_scrape['_status']=='ok').sum())} | err={int((df_scrape['_status']=='error').sum())}")

    # 8) Merge (left join por URL si hay GSC)
    if df_gsc.empty:
        df_merge = df_scrape.copy()
    else:
        df_merge = pd.merge(df_gsc, df_scrape, how="left", left_on="url", right_on="url")

    # 9) Post-filtros sección payload (si existieran)
    sections_payload = (cfg.get("filters") or {}).get("sections_payload")
    if sections_payload and isinstance(sections_payload, list):
        # Simple contiene: mantener si section en lista
        before = len(df_merge)
        df_merge = df_merge[df_merge["section"].isin(sections_payload)]
        dbg(f"Filtro sections_payload: {before} -> {len(df_merge)}")

    # 10) Archivo de salida
    # Nombre: sitio + fechas
    base_domain = domain_from_urls(urls_to_scrape) if urls_to_scrape else (domain_from_urls(seeds_filtered) or "sitio")
    span = date_span_for_filename(start_date, end_date)
    out_name = f"extract_{base_domain}_{span}.xlsx"

    # 11) Escribir Excel (si no hay filas, igual escribimos con mensaje)
    if df_merge.empty and df_scrape.empty:
        dbg("[WARN] Merge y Extract vacíos. Escribiendo archivo con hoja debug para diagnóstico.")
        return write_excel_empty(cfg, start_date, end_date, debug_lines, out_name)

    write_excel(out_name, df_merge, df_scrape, debug_lines, start_date, end_date, cfg)
    dbg(f"Archivo escrito: {out_name}")
    dbg(f"Duración total: {time.time() - t0:.2f}s")
    return out_name


def write_excel(
    out_name: str,
    df_merge: pd.DataFrame,
    df_scrape: pd.DataFrame,
    debug_lines: List[str],
    start_date: str,
    end_date: str,
    cfg: Dict
) -> None:
    """Escribe 3 hojas: merge_gsc, extract, debug."""
    # Aseguramos columnas mínimas para que no falle la escritura
    if df_merge is None:
        df_merge = pd.DataFrame()
    if df_scrape is None:
        df_scrape = pd.DataFrame()

    # Debug DF
    dbg_rows = [{"step": i+1, "message": line} for i, line in enumerate(debug_lines)]
    dbg_rows.append({"step": len(dbg_rows)+1, "message": f"start_date={start_date} end_date={end_date}"})
    dbg_rows.append({"step": len(dbg_rows)+1, "message": f"source={cfg.get('source')} tipo={cfg.get('tipo')}"})
    dbg_rows.append({"step": len(dbg_rows)+1, "message": f"min_clicks={(cfg.get('filters') or {}).get('min_clicks')} min_impressions={(cfg.get('filters') or {}).get('min_impressions')}"})
    df_debug = pd.DataFrame(dbg_rows)

    # Engine preferido
    try:
        with pd.ExcelWriter(out_name, engine="xlsxwriter") as xw:
            df_merge.to_excel(xw, index=False, sheet_name="merge_gsc")
            df_scrape.to_excel(xw, index=False, sheet_name="extract")
            df_debug.to_excel(xw, index=False, sheet_name="debug")
    except Exception:
        # fallback a openpyxl
        with pd.ExcelWriter(out_name, engine="openpyxl", mode="w") as xw:
            df_merge.to_excel(xw, index=False, sheet_name="merge_gsc")
            df_scrape.to_excel(xw, index=False, sheet_name="extract")
            df_debug.to_excel(xw, index=False, sheet_name="debug")


def write_excel_empty(
    cfg: Dict,
    start_date: str,
    end_date: str,
    debug_lines: List[str],
    out_name: Optional[str] = None
) -> str:
    base_domain = domain_from_urls(normalize_url_list(cfg)) or "sitio"
    span = date_span_for_filename(start_date, end_date)
    out_name = out_name or f"extract_{base_domain}_{span}.xlsx"

    msg = {
        "mensaje": "Sin datos para escribir",
        "sugerencias": "Revisa filtros (min_impressions/min_clicks), 'source', 'tipo', y credenciales GSC.",
        "start_date": start_date,
        "end_date": end_date
    }
    df_empty_merge = pd.DataFrame([msg])
    df_empty_extract = pd.DataFrame([msg])
    df_debug = pd.DataFrame([{"step": i+1, "message": line} for i, line in enumerate(debug_lines)])

    try:
        with pd.ExcelWriter(out_name, engine="xlsxwriter") as xw:
            df_empty_merge.to_excel(xw, index=False, sheet_name="merge_gsc")
            df_empty_extract.to_excel(xw, index=False, sheet_name="extract")
            df_debug.to_excel(xw, index=False, sheet_name="debug")
    except Exception:
        with pd.ExcelWriter(out_name, engine="openpyxl", mode="w") as xw:
            df_empty_merge.to_excel(xw, index=False, sheet_name="merge_gsc")
            df_empty_extract.to_excel(xw, index=False, sheet_name="extract")
            df_debug.to_excel(xw, index=False, sheet_name="debug")

    print(f"[INFO] Archivo (vacío/diagnóstico) escrito: {out_name}")
    return out_name


# ---------------- CLI ---------------- #

def load_config_from_stdin() -> Dict:
    raw = sys.stdin.read()
    return json.loads(raw)


def load_config_from_file(path: str) -> Dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(description="Analizador SEO - GSC + Scrape -> Excel")
    parser.add_argument("--config", type=str, help="Ruta al config.json")
    parser.add_argument("--stdin", action="store_true", help="Leer config JSON desde stdin")
    args = parser.parse_args()

    if not args.stdin and not args.config:
        print("[ERROR] Especificá --config config.json o --stdin", file=sys.stderr)
        sys.exit(2)

    try:
        cfg = load_config_from_stdin() if args.stdin else load_config_from_file(args.config)
    except Exception as e:
        print(f"[FATAL] No pude cargar el JSON de config: {e}", file=sys.stderr)
        sys.exit(3)

    # Normalización de campos clave para evitar errores tontos
    cfg.setdefault("filters", {})
    cfg.setdefault("scrape", {})
    cfg.setdefault("output", {})
    cfg.setdefault("selectors", {})
    cfg["filters"].setdefault("min_clicks", 0)
    cfg["filters"].setdefault("min_impressions", 0)
    cfg.setdefault("max_urls", 300)
    cfg.setdefault("order_by", "clicks")
    cfg.setdefault("tipo", "web")   # 'Ambos' rompe GSC; usamos 'web' por defecto

    try:
        out_name = run_pipeline(cfg)
        print(f"[OK] Listo -> {out_name}")
    except Exception as e:
        print(f"[FATAL] Error general del pipeline: {e}", file=sys.stderr)
        # En caso de crash, tratamos de escribir un archivo de diagnóstico
        try:
            start_date, end_date = compute_window(cfg.get("window") or {}, safe_int(cfg.get("lag_days"), 0))
            write_excel_empty(cfg, start_date, end_date, [f"Crash: {e}"])
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()