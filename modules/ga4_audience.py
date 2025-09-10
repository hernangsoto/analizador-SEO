# modules/ga4_audience.py
from __future__ import annotations

"""
Reporte de audiencia (GA4)
- Crea un Google Sheet y publica:
  1) Audiencia por país + device (activeUsers, newUsers, sessions)
  2) Serie diaria (activeUsers, sessions)
  3) URLs (Top) — landingPage o pagePath + métricas (N configurable)
  4) URL × País+Device — desglose para las Top N URLs
  5) Serie diaria por URL (Top N) — long-form
- Defensas:
  * Corrige si drive_service y gs_client llegan invertidos.
  * Normaliza fechas (date/datetime/str).
  * Si GA4 falla, deja el error documentado en la hoja.
- Params opcionales para desgloses por URL:
  * urls_top_n: int = 20
  * url_dimension: str = "landingPagePlusQueryString"  # o "pagePathPlusQueryString"
  * include_url_series: bool = True
  * include_url_country_device: bool = True
"""

from typing import Any, Tuple, Optional, Iterable, List, Dict
from datetime import date, datetime, timedelta
import pandas as pd


# ----------------------------
# Utilidades
# ----------------------------
def _ensure_drive_and_gspread(
    drive_service: Any,
    gs_client: Any
) -> Tuple[Any, Any]:
    """
    Asegura (drive_service, gs_client) en el orden correcto.
    drive_service debe tener .files(); gs_client debe tener .open_by_key.
    """
    has_files_a = hasattr(drive_service, "files")
    has_files_b = hasattr(gs_client, "files")
    has_open_a = hasattr(drive_service, "open_by_key")
    has_open_b = hasattr(gs_client, "open_by_key")

    # Caso correcto
    if has_files_a and has_open_b:
        return drive_service, gs_client
    # Invertidos
    if has_files_b and has_open_a:
        return gs_client, drive_service
    # Heurísticas extra
    if has_files_b:
        return gs_client, drive_service
    if has_open_a:
        return gs_client, drive_service
    return drive_service, gs_client


def _as_date(d: Any) -> date:
    """Convierte date/datetime/str(YYYY-MM-DD) → date."""
    if isinstance(d, date) and not isinstance(d, datetime):
        return d
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, str):
        s = d.strip()
        # ISO estándar primero
        try:
            return datetime.fromisoformat(s).date()
        except Exception:
            pass
        # Fallback muy permisivo
        try:
            y = int(s[0:4]); m = int(s[5:7]); dd = int(s[8:10])
            return date(y, m, dd)
        except Exception:
            raise ValueError(f"Fecha inválida: {d!r}. Formato esperado YYYY-MM-DD.")
    raise ValueError(f"No puedo interpretar la fecha: {d!r}")


def _pick_win(params: dict) -> Tuple[date, date, int]:
    """Obtiene (start, end, lag_days) desde params con defaults sensatos."""
    lag = int(params.get("lag_days", 3))
    # diversas llaves compatibles
    start = params.get("start") or params.get("start_date") or (params.get("window") or {}).get("start")
    end   = params.get("end")   or params.get("end_date")   or (params.get("window") or {}).get("end")

    if not (start and end):
        # fallback: últimos 28 días cerrados por lag
        end_dt = date.today() - timedelta(days=lag)
        start_dt = end_dt - timedelta(days=27)
        return start_dt, end_dt, lag

    return _as_date(start), _as_date(end), lag


def _ga4_run_report(
    ga4_client: Any,
    property_id: str,
    dimensions: List[str],
    metrics: List[str],
    start: date,
    end: date,
    limit: int = 250000,
    dimension_filter: Optional[Dict[str, Any]] = None,
    order_bys: Optional[List[Dict[str, Any]]] = None,
) -> pd.DataFrame:
    """
    Ejecuta run_report y devuelve DataFrame.
    Permite dimension_filter y order_bys usando el mapeo dict de la API.
    """
    req: Dict[str, Any] = {
        "property": f"properties/{property_id}",
        "date_ranges": [{"start_date": str(start), "end_date": str(end)}],
        "dimensions": [{"name": d} for d in dimensions],
        "metrics": [{"name": m} for m in metrics],
        "limit": limit
    }
    if dimension_filter:
        # Estructura esperada: DimensionFilterExpression
        # Ej: {"filter":{"field_name": "landingPagePlusQueryString","in_list_filter":{"values": [...]}}}
        req["dimension_filter"] = dimension_filter
    if order_bys:
        # Ej: [{"metric": {"metric_name": "sessions"}, "desc": True}]
        req["order_bys"] = order_bys

    resp = ga4_client.run_report(request=req)

    dim_names = [d.name for d in resp.dimension_headers] if getattr(resp, "dimension_headers", None) else []
    met_names = [m.name for m in resp.metric_headers] if getattr(resp, "metric_headers", None) else []

    rows = []
    for r in (resp.rows or []):
        dvals = [dv.value for dv in r.dimension_values] if getattr(r, "dimension_values", None) else []
        mvals = [mv.value for mv in r.metric_values] if getattr(r, "metric_values", None) else []
        rec = {k: v for k, v in zip(dim_names, dvals)}
        for k, v in zip(met_names, mvals):
            # valores métricos vienen como str → intentar numeric
            try:
                if v is None:
                    rec[k] = 0
                elif isinstance(v, str) and (("." in v) or ("e" in v.lower())):
                    rec[k] = float(v)
                else:
                    rec[k] = int(v)
            except Exception:
                rec[k] = v
        rows.append(rec)

    df = pd.DataFrame(rows)
    return df


def _gspread_write_df(ws, df: pd.DataFrame) -> None:
    """Escribe DataFrame en una worksheet (borra y pega)."""
    if df is None or df.empty:
        ws.clear()
        ws.update([["(sin datos)"]])
        return
    ws.clear()
    values = [df.columns.tolist()] + df.astype(str).fillna("").values.tolist()
    ws.update(values)


# ----------------------------
# Runner principal
# ----------------------------
def run_ga4_audience_report(
    ga4_data,           # GA4 Data API client
    drive_service,      # Google Drive API service (tiene .files())
    gs_client,          # gspread client (tiene .open_by_key)
    property_id: str,
    params: dict,
    dest_folder_id: Optional[str] = None,
) -> Optional[str]:
    """
    Genera un Sheet con 5 pestañas:
      - 'Audiencia país+device'
      - 'Serie diaria'
      - 'URLs (Top)'
      - 'URL × País+Device'
      - 'Serie diaria por URL (Top N)'
    Devuelve el Spreadsheet ID o None si falla antes de crear el archivo.
    """
    # 1) Normalizaciones y defensas
    drive_service, gs_client = _ensure_drive_and_gspread(drive_service, gs_client)
    start, end, lag = _pick_win(params or {})
    span_days = (end - start).days + 1
    if span_days <= 0:
        raise ValueError(f"Rango inválido: {start} → {end}")

    prop_label = params.get("property_label") or params.get("property_name") or str(property_id)
    sheet_name = f"GA4 Audiencia ({start} a {end}) - {prop_label}"

    # Parámetros para desgloses de URL
    urls_top_n = int(params.get("urls_top_n", 20))
    url_dimension = str(params.get("url_dimension", "landingPagePlusQueryString")).strip() or "landingPagePlusQueryString"
    include_url_series = bool(params.get("include_url_series", True))
    include_url_country_device = bool(params.get("include_url_country_device", True))

    # 2) Crear el Spreadsheet ANTES de llamar a GA4 (así siempre retornamos un ID)
    meta = {"name": sheet_name, "mimeType": "application/vnd.google-apps.spreadsheet"}
    if dest_folder_id:
        meta["parents"] = [dest_folder_id]

    try:
        newfile = drive_service.files().create(body=meta, fields="id,name,webViewLink").execute()
        sid = newfile["id"]
    except Exception as e:
        # Si esto falla, no hay ID para devolver
        raise RuntimeError(f"No pude crear el Sheet en Drive: {e}")

    # Abrir con gspread
    try:
        sh = gs_client.open_by_key(sid)
        ws_main = sh.sheet1
        ws_main.update_title("Audiencia país+device")
        # Precrear otras pestañas por si fallan más adelante
        def _ensure_ws(title: str):
            try:
                return sh.worksheet(title)
            except Exception:
                return sh.add_worksheet(title=title, rows=100, cols=26)
        ws_series = _ensure_ws("Serie diaria")
        ws_urls   = _ensure_ws("URLs (Top)")
        ws_ud     = _ensure_ws("URL × País+Device")
        ws_us     = _ensure_ws("Serie diaria por URL (Top N)")
    except Exception:
        # Tuvimos ID pero no pudimos abrir con gspread → devolver ID igual
        try:
            drive_service.files().update(fileId=sid, body={"name": sheet_name + " (sin contenido)"}).execute()
        except Exception:
            pass
        return sid

    # 3) Traer datos GA4
    try:
        # Pestaña 1: país + device
        dims_1 = ["country", "deviceCategory"]
        mets_1 = ["activeUsers", "newUsers", "sessions"]
        df1 = _ga4_run_report(ga4_data, property_id, dims_1, mets_1, start, end)
        if not df1.empty:
            df1 = df1.groupby(dims_1, as_index=False).agg(
                activeUsers=("activeUsers", "sum"),
                newUsers=("newUsers", "sum"),
                sessions=("sessions", "sum"),
            ).sort_values(["activeUsers"], ascending=False)
        _gspread_write_df(ws_main, df1)

        # Pestaña 2: serie por día
        dims_2 = ["date"]
        mets_2 = ["activeUsers", "sessions"]
        df2 = _ga4_run_report(ga4_data, property_id, dims_2, mets_2, start, end)
        if not df2.empty:
            # Normalizar 'date' a YYYY-MM-DD si llega como '20240910'
            if "date" in df2.columns:
                def _fmt(dv: str) -> str:
                    s = str(dv)
                    if len(s) == 8 and s.isdigit():
                        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
                    return s
                df2["date"] = df2["date"].map(_fmt)
            df2 = df2.sort_values("date")
        _gspread_write_df(ws_series, df2)

        # ---------- Desgloses por URL ----------
        # (a) URLs (Top)
        dims_u = [url_dimension]
        mets_u = ["activeUsers", "newUsers", "sessions"]
        # Ordenar por sesiones descendente desde la API cuando sea posible
        order_bys = [{"metric": {"metric_name": "sessions"}, "desc": True}]
        df_urls_all = _ga4_run_report(
            ga4_data, property_id, dims_u, mets_u, start, end,
            order_bys=order_bys
        )
        if not df_urls_all.empty:
            df_urls_top = (
                df_urls_all.groupby(dims_u, as_index=False)
                .agg(activeUsers=("activeUsers", "sum"),
                     newUsers=("newUsers", "sum"),
                     sessions=("sessions", "sum"))
                .sort_values("sessions", ascending=False)
            )
            if urls_top_n > 0:
                df_urls_top = df_urls_top.head(urls_top_n)
        else:
            df_urls_top = pd.DataFrame(columns=[url_dimension] + mets_u)
        _gspread_write_df(ws_urls, df_urls_top)

        # Lista de URLs top para filtros siguientes
        top_values: List[str] = df_urls_top[url_dimension].astype(str).tolist() if not df_urls_top.empty else []

        # (b) URL × País+Device (si se pide y hay top_values)
        if include_url_country_device and top_values:
            dim_filter = {
                "filter": {
                    "field_name": url_dimension,
                    "in_list_filter": {"values": top_values}
                }
            }
            dims_ud = [url_dimension, "country", "deviceCategory"]
            mets_ud = ["activeUsers", "newUsers", "sessions"]
            df_ud = _ga4_run_report(
                ga4_data, property_id, dims_ud, mets_ud, start, end,
                dimension_filter=dim_filter
            )
            if not df_ud.empty:
                df_ud = df_ud.groupby(dims_ud, as_index=False).agg(
                    activeUsers=("activeUsers", "sum"),
                    newUsers=("newUsers", "sum"),
                    sessions=("sessions", "sum"),
                ).sort_values([url_dimension, "sessions"], ascending=[True, False])
            _gspread_write_df(ws_ud, df_ud)
        else:
            _gspread_write_df(ws_ud, pd.DataFrame())

        # (c) Serie diaria por URL (Top N) (si se pide y hay top_values)
        if include_url_series and top_values:
            dim_filter = {
                "filter": {
                    "field_name": url_dimension,
                    "in_list_filter": {"values": top_values}
                }
            }
            dims_us = ["date", url_dimension]
            mets_us = ["activeUsers", "sessions"]
            df_us = _ga4_run_report(
                ga4_data, property_id, dims_us, mets_us, start, end,
                dimension_filter=dim_filter
            )
            if not df_us.empty:
                # normalizar fecha
                if "date" in df_us.columns:
                    def _fmt2(dv: str) -> str:
                        s = str(dv)
                        if len(s) == 8 and s.isdigit():
                            return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
                        return s
                    df_us["date"] = df_us["date"].map(_fmt2)
                df_us = df_us.sort_values(["date", url_dimension])
            _gspread_write_df(ws_us, df_us)
        else:
            _gspread_write_df(ws_us, pd.DataFrame())

        # Info meta en una pestaña opcional
        try:
            ws_meta = None
            try:
                ws_meta = sh.worksheet("Meta")
            except Exception:
                ws_meta = sh.add_worksheet(title="Meta", rows=100, cols=8)
            info = pd.DataFrame({
                "campo": [
                    "property_id", "property_label", "start", "end", "lag_days", "span_days",
                    "url_dimension", "urls_top_n", "include_url_series", "include_url_country_device"
                ],
                "valor": [
                    property_id, prop_label, str(start), str(end), lag, span_days,
                    url_dimension, urls_top_n, include_url_series, include_url_country_device
                ],
            })
            _gspread_write_df(ws_meta, info)
        except Exception:
            pass

    except Exception as e:
        # Escribir el error visible en la primera hoja
        error_text = f"❌ Error al consultar/armar GA4: {e}"
        try:
            ws_main.clear()
            ws_main.update([["Error"], [error_text]])
        except Exception:
            pass

    return sid
