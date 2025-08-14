# modules/drive.py (solo la función)
import time
from googleapiclient.errors import HttpError
import gspread

def copy_template_and_open(drive, gsclient, template_id: str, title: str, dest_folder_id: str | None = None):
    # (si ya tienes verify_template_access, déjalo igual antes de esto)
    body = {"name": title}
    if dest_folder_id:
        body["parents"] = [dest_folder_id]

    try:
        new_file = drive.files().copy(
            fileId=template_id, body=body, supportsAllDrives=True
        ).execute()
    except HttpError as e:
        raise RuntimeError(f"Falló la copia del template: {e}")

    sid = new_file["id"]

    # Esperar a que la copia esté disponible en Sheets (propagación)
    last_err = None
    for i in range(7):  # ~0.3s + 0.5s + ... ≈ 3s
        try:
            sh = gsclient.open_by_key(sid)
            # tocar metadata de worksheets para forzar inicialización (sin escribir nada)
            _ = [ws.title for ws in sh.worksheets()]
            return sh, sid
        except Exception as e:
            last_err = e
            time.sleep(0.3 + i * 0.4)

    raise RuntimeError(f"No pude abrir el nuevo Sheets (id={sid}). Detalle: {last_err}")
