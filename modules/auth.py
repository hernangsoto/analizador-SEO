# modules/auth.py
from __future__ import annotations

from typing import Optional, List, Dict, Tuple
import os
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

from urllib.parse import urlsplit, parse_qs
import requests
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
import streamlit as st

from .utils import debug_log, token_store

# =============================
# Scopes
# =============================
SCOPES_OIDC: List[str] = ["openid", "email", "profile"]

SCOPES_DRIVE: List[str] = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
SCOPES_GSC: List[str] = [
    "https://www.googleapis.com/auth/webmasters.readonly",
]

SCOPES_PERSONAL_FULL: List[str] = [*SCOPES_OIDC, *SCOPES_DRIVE, *SCOPES_GSC]

def creds_to_dict(creds: Credentials) -> dict:
    return {
        "token": creds.token,
        "refresh_token": getattr(creds, "refresh_token", None),
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes) if getattr(creds, "scopes", None) else None,
    }

# =============================
# Cliente OAuth "installed" (copy/paste a http://localhost)
# – lo usamos para las cuentas ACCESO / ACCESO_MEDIOS (SC)
# =============================
def build_flow(account_key: str, scopes: List[str]) -> Flow:
    try:
        acc = st.secrets["accounts"][account_key]
    except Exception:
        st.error(f"No encontré credenciales en st.secrets['accounts']['{account_key}'].")
        st.stop()
    client_secrets = {
        "installed": {
            "client_id": acc["client_id"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_secret": acc["client_secret"],
            "redirect_uris": ["http://localhost"],
        }
    }
    flow = Flow.from_client_config(client_secrets, scopes=scopes)
    flow.redirect_uri = "http://localhost"
    return flow

# =============================
# Paso 0 (botón Google) — cliente OAuth WEB (redirect a tu dominio)
# lee [auth] client_id, client_secret, redirect_uri
# =============================
def build_flow_web(*args, **kwargs) -> Flow:
    # Permitir build_flow_web(scopes) o build_flow_web(account_key, scopes)
    account_key: Optional[str] = None
    scopes: List[str] = []
    if len(args) == 1 and isinstance(args[0], list):
        scopes = args[0]
    elif len(args) >= 2 and isinstance(args[1], list):
        account_key = args[0]; scopes = args[1]
    else:
        scopes = kwargs.get("scopes", [])
        account_key = kwargs.get("account_key")

    auth = st.secrets.get("auth", {}) or {}
    cid = auth.get("client_id")
    csec = auth.get("client_secret")
    ruri = auth.get("redirect_uri")
    if not (cid and csec and ruri):
        raise RuntimeError(
            "Falta configurar el redirect_uri o el cliente OAuth Web.\n"
            "Define en secrets [auth] client_id, client_secret y redirect_uri."
        )
    client_secrets = {
        "web": {
            "client_id": cid,
            "client_secret": csec,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [ruri],
        }
    }
    flow = Flow.from_client_config(client_secrets, scopes=scopes)
    flow.redirect_uri = ruri
    return flow

def fetch_userinfo(creds: Credentials) -> Dict[str, str]:
    info: Dict[str, str] = {}
    try:
        resp = requests.get(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers={"Authorization": f"Bearer {creds.token}"},
            timeout=10,
        )
        if resp.status_code == 200:
            j = resp.json()
            info = {
                "name": j.get("name") or j.get("email") or "Invitado",
                "email": j.get("email") or "—",
                "picture": j.get("picture"),
            }
    except Exception as e:
        debug_log("[fetch_userinfo] fallo userinfo", str(e))
    return info

# =============================
# Cache (compat)
# =============================
def get_cached_personal_creds() -> Optional[Credentials]:
    creds = token_store.as_credentials("creds_dest")
    if creds:
        return creds
    data = token_store.load("creds_dest") or st.session_state.get("creds_dest")
    if data:
        try:
            return Credentials(**data)
        except Exception as e:
            debug_log("[get_cached_personal_creds] no pude construir Credentials desde dict", str(e))
    return None