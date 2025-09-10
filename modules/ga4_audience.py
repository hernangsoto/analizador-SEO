# modules/ga4_audience.py
from __future__ import annotations

"""
Reporte de audiencia (GA4)
- Crea un Google Sheet y publica:
  1) Audiencia por país + device (activeUsers, newUsers, sessions [, video_*] [, ev_*])
  2) Serie diaria (activeUsers, sessions [, video_*] [, ev_*])
  3) URLs (Top) — métricas de audiencia [+ video_*] [+ ev_*]
  4) URL × País+Device — desglose para las Top N [+ video_*] [+ ev_*]
  5) Serie diaria por URL (Top N) — long-form [+ video_*] [+ ev_*]

Opciones (en params):
  - urls_top_n: int = 20
  - url_dimension: str = "landingPagePlusQueryString"  # o "pagePathPlusQueryString"
  - include_url_series: bool = True
  - include_url_country_device: bool = True
  - include_video_metrics: bool = False
  - video_event_names: list[str] = ["video_start","video_complete"]
  - custom_event_names: list[str] = []   # <- NUEVO: lista de eventName GA4 a sumar (crea columnas ev_<evento>)
"""

from typing import Any, Tuple, Optional, List, Dict
from datetime import date, datetime, timedelta
import pandas as pd
import re


# ----------------------------
# Utilidades
# ----------------------------
def _ensure_drive_and_gspread(
    drive_service: Any,
    gs_client: Any
) -> Tuple[Any, Any]:
    has_files_a = hasattr(drive_service, "files")
    has_files_b = hasattr(gs_client, "files")
    has_open_a = hasattr(drive_service, "open_by_key")
    has_open_b = hasattr(gs_client, "open_by_key")

    if has_files_a and has_open_b:
        return drive_service, gs_client
    if has_files_b and has_open_a:
        return gs_client, drive_service
    if has_files_b:
        return gs_client, drive_service
    if has_open_a:
        return gs_client, drive_service
    return drive_service, gs_client


def _as_date(d: Any) -> date:
    if isinstance(d, date) and not isinstance(d, datetime):
        return d
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, str):
        s = d.strip()
        try:
            return datetime.fromisoformat(s).date()
        except Exception:
            pass
        try:
            y = int(s[0:4]); m = int(s[5:7]); dd = int(s[8:10])
            return date(y, m, dd)
        except Exception:
            raise ValueError(f"Fecha inválida: {d!r}. Formato esperado YYYY-MM-DD.")
    raise ValueError(f"No puedo interpretar la fecha: {d!r}")


def _pick_win(params: dict) -> Tuple[date, date, int]:
    lag = int(params.get("lag_days", 3))
    start = params.get("start") or params.get("start_date") or (params.get("window") or {}).get("start")
    end   = params.get("end")   or params.get("end_date")   or (params.get("window") or {}).get("end")
    if not (start and end):
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
    req: Dict[str, Any] = {
        "property": f"properties/{property_id}",
        "date_ranges": [{"start_date": str(start), "end_date": str(end)}],
        "dimensions": [{"name": d} for d in dimensions],
        "metrics": [{"name": m} for m in metrics],
        "limit": limit
    }
    if dimension_filter:
        req["dimension_filter"] = dimension_filter
    if order_bys:
        req["order_bys"] = order_bys

    resp = ga4_client.run_report(request=req)
    dim_names = [d.name for d in getattr(resp, "dimension_headers", [])]
    met_names = [m.name for m in getattr(resp, "metric_headers", [])]

    rows = []
    for r in (resp.rows or []):
        dvals = [dv.value for dv in getattr(r, "dimension_values", [])]
        mvals = [mv.value for mv in getattr(r, "metric_values", [])]
        rec = {k: v for k, v in zip(dim_names, dvals)}
        for k, v in zip(met_names, mvals):
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

    return pd.DataFrame(rows)


def _gspread_write_df(ws, df: pd.DataFrame) -> None:
    if df is None or df.empty:
        ws.clear()
        ws.update([["(sin datos)"]])
        return
    ws.clear()
    ws.update([df.columns.tolist()] + df.astype(str).fillna("").values.tolist())


# ---------- Filtros y helpers ----------
def _in_list_filter(field: str, values: List[str]) -> Dict[str, Any]:
    # ¡OJO! GA4 suele hacer match exacto en eventName; por eso respeta mayúsculas/minúsculas tal como están en GA4.
    return {"filter": {"field_name": field, "in_list_filter": {"values": values}}}

def _and_group(*exprs: Dict[str, Any]) -> Dict[str, Any]:
    return {"and_group": {"expressions": [e for e in exprs if e]}}

def _fmt_date8(dv: str) -> str:
    s = str(dv)
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return s

def _slug(s: str) -> str:
    # encabezado seguro tipo snake: mantiene letras/números/guiones bajos
    base = re.sub(r"[^0-9A-Za-z_]+", "_", str(s).strip())
    base = re.sub(r"_+", "_", base).strip("_")
    return base or "event"


# ---------- Video y eventos personalizados ----------
def _pivot_video_counts(df: pd.DataFrame, dims: List[str]) -> pd.DataFrame:
    if df.empty:
        out = pd.DataFrame(columns=dims + ["video_starts", "video_completes"])
        return out
    g = df.groupby(dims + ["eventName"], as_index=False).agg(eventCount=("eventCount", "sum"))
    pv = g.pivot_table(index=dims, columns="eventName", values="eventCount", aggfunc="sum", fill_value=0)
    pv = pv.reset_index()
    col_map = {}
    if "video_start" in pv.columns:
        col_map["video_start"] = "video_starts"
    if "video_complete" in pv.columns:
        col_map["video_complete"] = "video_completes"
    pv = pv.rename(columns=col_map)
    for want in ("video_starts", "video_completes"):
        if want not in pv.columns:
            pv[want] = 0
    return pv[dims + ["video_starts", "video_completes"]]


def _video_counts_by(
    ga4_client: Any,
    property_id: str,
    start: date,
    end: date,
    dims: List[str],
    event_names: List[str],
    url_dim: Optional[str] = None,
    url_values: Optional[List[str]] = None,
) -> pd.DataFrame:
    expr_event = _in_list_filter("eventName", event_names)
    expr_url = _in_list_filter(url_dim, url_values) if (url_dim and url_values) else None
    dim_filter = _and_group(expr_event, expr_url) if expr_url else expr_event

    df = _ga4_run_report(
        ga4_client, property_id,
        dimensions=dims + ["eventName"],
        metrics=["eventCount"],
        start=start, end=end,
        dimension_filter=dim_filter,
    )
    if "date" in df.columns:
        df["date"] = df["date"].map(_fmt_date8)
    return _pivot_video_counts(df, dims)


def _event_counts_by(
    ga4_client: Any,
    property_id: str,
    start: date,
    end: date,
    dims: List[str],
    event_names: List[str],
    url_dim: Optional[str] = None,
    url_values: Optional[List[str]] = None,
    prefix: str = "ev_",
) -> pd.DataFrame:
    """
    Devuelve DataFrame con dims + columnas ev_<slug(eventName)> para cada nombre en event_names.
    """
    if not event_names:
        return pd.DataFrame(columns=dims)

    expr_event = _in_list_filter("eventName", event_names)
    expr_url = _in_list_filter(url_dim, url_values) if (url_dim and url_values) else None
    dim_filter = _and_group(expr_event, expr_url) if expr_url else expr_event

    df = _ga4_run_report(
        ga4_client, property_id,
        dimensions=dims + ["eventName"],
        metrics=["eventCount"],
        start=start, end=end,
        dimension_filter=dim_filter,
    )
    if df.empty:
        out = pd.DataFrame({d: [] for d in dims})
        for ev in event_names:
            out[f"{prefix}{_slug(ev)}"] = []
        return out

    if "date" in df.columns:
        df["date"] = df["date"].map(_fmt_date8)

    g = df.groupby(dims + ["eventName"], as_index=False).agg(eventCount=("eventCount", "sum"))
    pv = g.pivot_table(index=dims, columns="eventName", values="eventCount", aggfunc="sum", fill_value=0).reset_index()

    # renombrar columnas a ev_<slug>
    ren = {}
    for c in pv.columns:
        if c in dims: 
            continue
        ren[c] = f"{prefix}{_slug(str(c))}"
    pv = pv.rename(columns=ren)

    # asegurar columnas para todos los pedidos
    for ev in event_names:
        col = f"{prefix}{_slug(ev)}"
        if col not in pv.columns:
            pv[col] = 0

    keep = dims + [f"{prefix}{_slug(ev)}" for ev in event_names]
    return pv[keep]


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
    drive_service, gs_client = _ensure_drive_and_gspread(drive_service, gs_client)
    start, end, lag = _pick_win(params or {})
    span_days = (end - start).days + 1
    if span_days <= 0:
        raise ValueError(f"Rango inválido: {start} → {end}")

    prop_label = params.get("property_label") or params.get("property_name") or str(property_id)
    sheet_name = f"GA4 Audiencia ({start} a {end}) - {prop_label}"

    # URL / Top N
    urls_top_n = int(params.get("urls_top_n", 20))
    url_dimension = str(params.get("url_dimension", "landingPagePlusQueryString")).strip() or "landingPagePlusQueryString"
    include_url_series = bool(params.get("include_url_series", True))
    include_url_country_device = bool(params.get("include_url_country_device", True))

    # Video + Eventos personalizados
    include_video_metrics = bool(params.get("include_video_metrics", False))
    video_event_names = list(params.get("video_event_names", ["video_start", "video_complete"]))
    custom_event_names: List[str] = list(params.get("custom_event_names", []))

    # 2) Crear Sheet
    meta = {"name": sheet_name, "mimeType": "application/vnd.google-apps.spreadsheet"}
    if dest_folder_id:
        meta["parents"] = [dest_folder_id]
    try:
        newfile = drive_service.files().create(body=meta, fields="id,name,webViewLink").execute()
        sid = newfile["id"]
    except Exception as e:
        raise RuntimeError(f"No pude crear el Sheet en Drive: {e}")

    # Abrir con gspread
    try:
        sh = gs_client.open_by_key(sid)
        ws_main = sh.sheet1
        ws_main.update_title("Audiencia país+device")
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
        try:
            drive_service.files().update(fileId=sid, body={"name": sheet_name + " (sin contenido)"}).execute()
        except Exception:
            pass
        return sid

    # 3) Datos GA4
    try:
        # --- (1) País + Device
        dims_1 = ["country", "deviceCategory"]
        mets_1 = ["activeUsers", "newUsers", "sessions"]
        df1 = _ga4_run_report(ga4_data, property_id, dims_1, mets_1, start, end)
        if not df1.empty:
            df1 = df1.groupby(dims_1, as_index=False).agg(
                activeUsers=("activeUsers", "sum"),
                newUsers=("newUsers", "sum"),
                sessions=("sessions", "sum"),
            ).sort_values(["activeUsers"], ascending=False)

        if include_video_metrics:
            v1 = _video_counts_by(ga4_data, property_id, start, end, dims=dims_1, event_names=video_event_names)
            df1 = df1.merge(v1, on=dims_1, how="left") if not df1.empty else v1

        if custom_event_names:
            c1 = _event_counts_by(ga4_data, property_id, start, end, dims=dims_1, event_names=custom_event_names)
            df1 = df1.merge(c1, on=dims_1, how="left") if not df1.empty else c1

        _gspread_write_df(ws_main, df1)

        # --- (2) Serie diaria
        dims_2 = ["date"]
        mets_2 = ["activeUsers", "sessions"]
        df2 = _ga4_run_report(ga4_data, property_id, dims_2, mets_2, start, end)
        if not df2.empty:
            df2["date"] = df2["date"].map(_fmt_date8)
            df2 = df2.sort_values("date")

        if include_video_metrics:
            v2 = _video_counts_by(ga4_data, property_id, start, end, dims=dims_2, event_names=video_event_names)
            df2 = df2.merge(v2, on=dims_2, how="left") if not df2.empty else v2

        if custom_event_names:
            c2 = _event_counts_by(ga4_data, property_id, start, end, dims=dims_2, event_names=custom_event_names)
            df2 = df2.merge(c2, on=dims_2, how="left") if not df2.empty else c2

        _gspread_write_df(ws_series, df2)

        # --- (3) URLs (Top)
        dims_u = [url_dimension]
        mets_u = ["activeUsers", "newUsers", "sessions"]
        df_urls_all = _ga4_run_report(
            ga4_data, property_id, dims_u, mets_u, start, end,
            order_bys=[{"metric": {"metric_name": "sessions"}, "desc": True}]
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
            df_urls_top = pd.DataFrame(columns=dims_u + mets_u)

        top_values = df_urls_top[url_dimension].astype(str).tolist() if not df_urls_top.empty else []

        if include_video_metrics and top_values:
            v_urls = _video_counts_by(
                ga4_data, property_id, start, end,
                dims=dims_u, event_names=video_event_names,
                url_dim=url_dimension, url_values=top_values
            )
            df_urls_top = df_urls_top.merge(v_urls, on=dims_u, how="left")

        if custom_event_names and top_values:
            c_urls = _event_counts_by(
                ga4_data, property_id, start, end,
                dims=dims_u, event_names=custom_event_names,
                url_dim=url_dimension, url_values=top_values
            )
            df_urls_top = df_urls_top.merge(c_urls, on=dims_u, how="left")

        _gspread_write_df(ws_urls, df_urls_top)

        # --- (4) URL × País+Device
        if include_url_country_device and top_values:
            dims_ud = [url_dimension, "country", "deviceCategory"]
            mets_ud = ["activeUsers", "newUsers", "sessions"]
            df_ud = _ga4_run_report(
                ga4_data, property_id, dims_ud, mets_ud, start, end,
                dimension_filter=_in_list_filter(url_dimension, top_values)
            )
            if not df_ud.empty:
                df_ud = df_ud.groupby(dims_ud, as_index=False).agg(
                    activeUsers=("activeUsers", "sum"),
                    newUsers=("newUsers", "sum"),
                    sessions=("sessions", "sum"),
                ).sort_values([url_dimension, "sessions"], ascending=[True, False])

            if include_video_metrics:
                v_ud = _video_counts_by(
                    ga4_data, property_id, start, end,
                    dims=dims_ud, event_names=video_event_names,
                    url_dim=url_dimension, url_values=top_values
                )
                df_ud = df_ud.merge(v_ud, on=dims_ud, how="left") if not df_ud.empty else v_ud

            if custom_event_names:
                c_ud = _event_counts_by(
                    ga4_data, property_id, start, end,
                    dims=dims_ud, event_names=custom_event_names,
                    url_dim=url_dimension, url_values=top_values
                )
                df_ud = df_ud.merge(c_ud, on=dims_ud, how="left") if not df_ud.empty else c_ud

            _gspread_write_df(ws_ud, df_ud)
        else:
            _gspread_write_df(ws_ud, pd.DataFrame())

        # --- (5) Serie diaria por URL (Top N)
        if include_url_series and top_values:
            dims_us = ["date", url_dimension]
            mets_us = ["activeUsers", "sessions"]
            df_us = _ga4_run_report(
                ga4_data, property_id, dims_us, mets_us, start, end,
                dimension_filter=_in_list_filter(url_dimension, top_values)
            )
            if not df_us.empty:
                df_us["date"] = df_us["date"].map(_fmt_date8)
                df_us = df_us.sort_values(["date", url_dimension])

            if include_video_metrics:
                v_us = _video_counts_by(
                    ga4_data, property_id, start, end,
                    dims=dims_us, event_names=video_event_names,
                    url_dim=url_dimension, url_values=top_values
                )
                df_us = df_us.merge(v_us, on=dims_us, how="left") if not df_us.empty else v_us

            if custom_event_names:
                c_us = _event_counts_by(
                    ga4_data, property_id, start, end,
                    dims=dims_us, event_names=custom_event_names,
                    url_dim=url_dimension, url_values=top_values
                )
                df_us = df_us.merge(c_us, on=dims_us, how="left") if not df_us.empty else c_us

            _gspread_write_df(ws_us, df_us)
        else:
            _gspread_write_df(ws_us, pd.DataFrame())

        # Meta
        try:
            ws_meta = None
            try:
                ws_meta = sh.worksheet("Meta")
            except Exception:
                ws_meta = sh.add_worksheet(title="Meta", rows=100, cols=8)
            info = pd.DataFrame({
                "campo": [
                    "property_id", "property_label", "start", "end", "lag_days", "span_days",
                    "url_dimension", "urls_top_n", "include_url_series", "include_url_country_device",
                    "include_video_metrics", "video_event_names", "custom_event_names"
                ],
                "valor": [
                    property_id, prop_label, str(start), str(end), lag, span_days,
                    url_dimension, urls_top_n, include_url_series, include_url_country_device,
                    include_video_metrics, ", ".join(video_event_names), ", ".join(custom_event_names)
                ],
            })
            _gspread_write_df(ws_meta, info)
        except Exception:
            pass

    except Exception as e:
        try:
            ws_main.clear()
            ws_main.update([["Error"], [f"❌ Error al consultar/armar GA4: {e}"]])
        except Exception:
            pass

    return sid
