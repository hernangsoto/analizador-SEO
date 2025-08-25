import re
from datetime import datetime
import streamlit as st

def _extract_medio_name(site_url: str | None) -> str | None:
    if not site_url:
        return None
    s = site_url.strip()
    if s.lower().startswith("sc-domain:"):
        return s.split(":", 1)[1].strip() or None
    return None

def maybe_prefix_sheet_name_with_medio(drive_service, file_id: str, site_url: str):
    medio = _extract_medio_name(site_url)
    if not medio:
        return
    medio = medio.strip().strip("-–—").strip()
    try:
        meta = drive_service.files().get(fileId=file_id, fields="name").execute()
        current = (meta.get("name") or "").strip()
        if re.match(rf"^{re.escape(medio)}\s*[-–—]\s+", current, flags=re.IGNORECASE):
            return
        current_no_lead = re.sub(r"^\s*[-–—]+\s*", "", current)
        new_name = f"{medio} - {current_no_lead}".strip()
        drive_service.files().update(fileId=file_id, body={"name": new_name}).execute()
    except Exception:
        pass

def _get_activity_log_config():
    cfg = st.secrets.get("activity_log", {}) or {}
    return {
        "title": cfg.get("title") or "Nomadic SEO – Activity Log",
        "worksheet": cfg.get("worksheet") or "Log",
        "file_id": cfg.get("file_id") or None,
        "folder_id": cfg.get("folder_id") or st.session_state.get("dest_folder_id"),
    }

def _get_or_create_activity_log_ws(drive, gsclient):
    cfg = _get_activity_log_config()
    file_id = cfg["file_id"]
    title = cfg["title"]
    ws_name = cfg["worksheet"]
    folder_id = cfg["folder_id"]

    try:
        if file_id:
            sh = gsclient.open_by_key(file_id)
        else:
            q = f"name = '{title}' and mimeType = 'application/vnd.google-apps.spreadsheet' and trashed = false"
            res = drive.files().list(
                q=q, spaces="drive",
                fields="files(id,name)",
                includeItemsFromAllDrives=True, supportsAllDrives=True
            ).execute()
            files = res.get("files", [])
            if files:
                file_id = files[0]["id"]
            else:
                body = {"name": title, "mimeType": "application/vnd.google-apps.spreadsheet"}
                if folder_id:
                    body["parents"] = [folder_id]
                new_file = drive.files().create(
                    body=body, fields="id", supportsAllDrives=True
                ).execute()
                file_id = new_file["id"]
        sh = gsclient.open_by_key(file_id)

        try:
            ws = sh.worksheet(ws_name)
        except Exception:
            try:
                ws = sh.sheet1
                ws.update_title(ws_name)
            except Exception:
                ws = sh.add_worksheet(title=ws_name, rows=1000, cols=20)

        headers = ["timestamp", "user_email", "event", "site_url", "analysis_kind", "sheet_id", "sheet_name", "sheet_url", "gsc_account", "notes"]
        try:
            top_left = ws.acell("A1").value
        except Exception:
            top_left = None
        if (top_left or "").strip().lower() != "timestamp":
            try:
                ws.clear()
            except Exception:
                pass
            ws.append_row(headers, value_input_option="USER_ENTERED")
        return ws, file_id
    except Exception:
        return None, None

def activity_log_append(drive, gsclient, *, user_email: str, event: str,
                        site_url: str = "", analysis_kind: str = "",
                        sheet_id: str = "", sheet_name: str = "", sheet_url: str = "",
                        gsc_account: str = "", notes: str = "") -> None:
    try:
        ws, _ = _get_or_create_activity_log_ws(drive, gsclient)
        if not ws:
            return
        ts = datetime.now().isoformat(timespec="seconds")
        row = [ts, user_email or "", event or "", site_url or "", analysis_kind or "",
               sheet_id or "", sheet_name or "", sheet_url or "", gsc_account or "", notes or ""]
        ws.append_row(row, value_input_option="USER_ENTERED")
    except Exception:
        pass