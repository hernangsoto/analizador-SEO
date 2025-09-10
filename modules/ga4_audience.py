from __future__ import annotations
from datetime import date, timedelta
import math
import pandas as pd

# Compatibilidad v1 / v1beta
try:
    from google.analytics.data_v1 import AnalyticsDataClient as _ADC
    from google.analytics.data_v1.types import (
        RunReportRequest, DateRange, Dimension, Metric, OrderBy,
        FilterExpression, Filter, FilterExpressionList
    )
except Exception:
    from google.analytics.data_v1beta import BetaAnalyticsDataClient as _ADC
    from google.analytics.data_v1beta.types import (
        RunReportRequest, DateRange, Dimension, Metric, OrderBy,
        FilterExpression, Filter, FilterExpressionList
    )

def _daterange(a: date, b: date):
    return DateRange(start_date=str(a), end_date=str(b))

def _to_df(resp) -> pd.DataFrame:
    if not hasattr(resp, "rows") or not resp.rows:
        return pd.DataFrame()
    cols = [h.name for h in resp.dimension_headers] + [h.name for h in resp.metric_headers]
    out = []
    for r in resp.rows:
        row = [dv.value for dv in r.dimension_values] + [mv.value for mv in r.metric_values]
        out.append(row)
    df = pd.DataFrame(out, columns=cols)
    # convertir métricas a numéricas
    for c in [h.name for h in resp.metric_headers]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df

def _order_by_metric(metric: str, desc: bool = True):
    return [OrderBy(metric=OrderBy.MetricOrderBy(metric_name=metric), desc=desc)]

def _order_by_dimension(dim: str, desc: bool = False):
    return [OrderBy(dimension=OrderBy.DimensionOrderBy(dimension_name=dim), desc=desc)]

def _run(client: _ADC, pid: str, start: date, end: date,
         dims: list[str], mets: list[str], limit: int = 10000,
         order_bys=None, dfilter: FilterExpression | None = None):
    req = RunReportRequest(
        property=f"properties/{pid}",
        date_ranges=[_daterange(start, end)],
        dimensions=[Dimension(name=d) for d in dims],
        metrics=[Metric(name=m) for m in mets],
        limit=limit
    )
    if order_bys:
        req.order_bys.extend(order_bys)
    if dfilter:
        req.dimension_filter = dfilter
    return client.run_report(req)

def _safe_report(client, pid, start, end, dims, mets, **kw) -> pd.DataFrame:
    try:
        return _to_df(_run(client, pid, start, end, dims, mets, **kw))
    except Exception:
        return pd.DataFrame()

def _total_row(df: pd.DataFrame, metric_cols: list[str]) -> dict:
    if df.empty:
        return {m: 0 for m in metric_cols}
    s = df[metric_cols].sum(numeric_only=True)
    return {m: float(s.get(m, 0)) for m in metric_cols}

def _delta_pct(cur: float, base: float) -> float:
    if base in (0, None) or math.isnan(base):
        return float("nan")
    return (cur - base) / base

def _write_df(sh, title: str, df: pd.DataFrame):
    if "Sheet1" in [w.title for w in sh.worksheets()]:
        ws = sh.sheet1
        try:
            ws.update_title(title)
        except Exception:
            pass
        ws.clear()
    else:
        ws = sh.add_worksheet(title=title, rows="1", cols="1")
    if df is None:
        df = pd.DataFrame()
    ws.update([df.columns.tolist()] + df.fillna("").astype(str).values.tolist())

def run_ga4_audience_report(ga4_data_client: _ADC, property_id: str,
                            drive_service, gs_client, params: dict,
                            dest_folder_id: str | None):
    start: date = params["start"]
    end: date   = params["end"]
    inc = params.get("include", {})
    top_n = int(params.get("top_n", 25))
    compare_prev = bool(params.get("compare_prev", True))
    compare_yoy  = bool(params.get("compare_yoy", True))

    # Períodos de comparación
    span = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=span - 1)
    yoy_start = start.replace(year=start.year - 1)
    yoy_end   = end.replace(year=end.year - 1)

    mets_core = ["activeUsers","newUsers","sessions","engagedSessions","engagementRate",
                 "averageSessionDuration","screenPageViews","bounceRate"]

    # ===== Datos base =====
    cur_tot = _safe_report(ga4_data_client, property_id, start, end, [], mets_core)
    prev_tot = _safe_report(ga4_data_client, property_id, prev_start, prev_end, [], mets_core) if compare_prev else pd.DataFrame()
    yoy_tot  = _safe_report(ga4_data_client, property_id, yoy_start, yoy_end, [], mets_core) if compare_yoy else pd.DataFrame()

    cur = _total_row(cur_tot, mets_core)
    prv = _total_row(prev_tot, mets_core) if compare_prev else {m: float("nan") for m in mets_core}
    yoy = _total_row(yoy_tot,  mets_core) if compare_yoy  else {m: float("nan") for m in mets_core}

    # Overview
    rows = []
    nice = {
        "activeUsers":"Usuarios",
        "newUsers":"Usuarios nuevos",
        "sessions":"Sesiones",
        "engagedSessions":"Sesiones con interacción",
        "engagementRate":"Tasa de interacción",
        "averageSessionDuration":"Duración media (s)",
        "screenPageViews":"Vistas",
        "bounceRate":"Tasa de rebote",
    }
    for m in mets_core:
        rows.append([
            nice[m],
            cur[m],
            prv[m] if compare_prev else "",
            _delta_pct(cur[m], prv[m]) if compare_prev else "",
            yoy[m] if compare_yoy else "",
            _delta_pct(cur[m], yoy[m]) if compare_yoy else "",
        ])
    df_overview = pd.DataFrame(rows, columns=[
        "Métrica","Actual","Previo","Δ vs. Previo","YoY","Δ vs. YoY"
    ])

    # Crear Sheet
    title = f"GA4 Audiencia {start}→{end}"
    meta = {"name": title, "mimeType": "application/vnd.google-apps.spreadsheet"}
    if dest_folder_id:
        meta["parents"] = [dest_folder_id]
    newfile = drive_service.files().create(body=meta, fields="id,name,webViewLink").execute()
    sid = newfile["id"]
    sh = gs_client.open_by_key(sid)

    _write_df(sh, "Overview", df_overview)

    # Time series
    if inc.get("timeseries", True):
        df_ts = _safe_report(ga4_data_client, property_id, start, end,
                             ["date"], ["activeUsers","sessions","engagedSessions"],
                             order_bys=_order_by_dimension("date"), limit=5000)
        _write_df(sh, "Serie diaria", df_ts)

    # Cortes (helper)
    def _top_sheet(name: str, dims: list[str], mets: list[str] = None, order_metric="activeUsers"):
        ms = mets or ["activeUsers","sessions","engagedSessions"]
        df = _safe_report(ga4_data_client, property_id, start, end,
                          dims, ms, order_bys=_order_by_metric(order_metric, True), limit=top_n)
        if not df.empty:
            # participación %
            total = df[order_metric].sum()
            if total and total > 0:
                df["share_"+order_metric] = (df[order_metric] / total).round(4)
        _write_df(sh, name, df)

    if inc.get("geo", True):
        _top_sheet("País", ["country"])
    if inc.get("language", True):
        _top_sheet("Idioma", ["language"])
    if inc.get("channels", True):
        _top_sheet("Canal (Default)", ["sessionDefaultChannelGroup"])
    if inc.get("device", True):
        _top_sheet("Dispositivo", ["deviceCategory"])
    if inc.get("os", True):
        _top_sheet("SO", ["operatingSystem"])
    if inc.get("browser", True):
        _top_sheet("Navegador", ["browser"])
    if inc.get("new_vs_returning", True):
        _top_sheet("Nuevo vs Recurrente", ["newVsReturning"])
    if inc.get("hour", True):
        df_hour = _safe_report(ga4_data_client, property_id, start, end,
                               ["hour"], ["activeUsers","sessions","engagedSessions"],
                               order_bys=_order_by_dimension("hour"), limit=24)
        _write_df(sh, "Hora del día", df_hour)
    if inc.get("demographics", True):
        # Se intenta y, si no hay señales / permisos, se omite silenciosamente
        try:
            _top_sheet("Edad", ["age"])
        except Exception:
            pass
        try:
            _top_sheet("Género", ["gender"])
        except Exception:
            pass

    return sid
