# modules/ga4_audience.py
from __future__ import annotations

"""
Reporte de audiencia (GA4)
- Crea un Google Sheet y publica dos pestañas:
  1) Audiencia por país + device (activeUsers, newUsers, sessions)
  2) Serie diaria (activeUsers, sessions)
- Defensas:
  * Corrige si drive_service y gs_client llegan invertidos.
  * Normaliza fechas (date/datetime/str).
  * Si GA4 falla, deja el error documentado en la hoja.
"""

from typing import Any, Tuple, Optional
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


def _ga4_run_report(ga4_client: Any, property_id: str,
                    dimensions: list[str], metrics: list[str],
                    start: date, end: date, limit: int = 250000) -> pd.DataFrame:
    """
    Ejecuta run_report y devuelve DataFrame.
    Usa dict request para evitar dependencia de clases protobuf explícitas.
    """
    req = {
        "property": f"properties/{property_id}",
        "date_ranges": [{"start_date": str(start), "end_date": str(end)}],
        "dimensions": [{"name": d} for d in dimensions],
        "metrics": [{"name": m} for m in metrics],
        "limit": limit
    }
    # La lib oficial acepta dict como request mapeable.
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
                elif "." in v or "e" in v.lower():
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
    Genera un Sheet con 2 pestañas:
      - 'Audiencia país+device'
      - 'Serie diaria'
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
        # Precrear segunda pestaña
        try:
            ws_series = sh.worksheet("Serie diaria")
        except Exception:
            ws_series = sh.add_worksheet(title="Serie diaria", rows=100, cols=20)
    except Exception:
        # Tuvimos ID pero no pudimos abrir con gspread → devolver ID igual
        try:
            # Dejar mensaje en título
            drive_service.files().update(fileId=sid, body={"name": sheet_name + " (sin contenido)"}).execute()
        except Exception:
            pass
        return sid

    # 3) Traer datos GA4
    error_text = None
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

        # Info meta en una pestaña opcional
        try:
            ws_meta = None
            try:
                ws_meta = sh.worksheet("Meta")
            except Exception:
                ws_meta = sh.add_worksheet(title="Meta", rows=50, cols=6)
            info = pd.DataFrame({
                "campo": ["property_id", "property_label", "start", "end", "lag_days", "span_days"],
                "valor": [property_id, prop_label, str(start), str(end), lag, span_days],
            })
            _gspread_write_df(ws_meta, info)
        except Exception:
            pass

    except Exception as e:
        # Escribir el error visible en la primera hoja
        error_text = f"❌ Error al consultar GA4: {e}"
        try:
            ws_main.clear()
            ws_main.update([["Error"], [error_text]])
        except Exception:
            pass

    return sid
