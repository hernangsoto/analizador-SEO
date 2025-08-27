# modules/auth.py
from __future__ import annotations

from typing import Optional, Tuple, List, Dict
import os
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

from urllib.parse import urlsplit, parse_qs
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
import streamlit as st

from .utils import debug_log, token_store

# =============================
# Scopes
# =============================
SCOPES_DRIVE: List[str] = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
SCOPES_GSC: List[str] = [
    "https://www.googleapis.com/auth/webmasters.readonly",
]
# OIDC para identidad (Paso 0)
SCOPES_OIDC: List[str] = ["openid", "email", "profile"]


# =============================
# Helpers comunes
# =============================
def _get_account(account_key: str) -> Dict[str, str]:
    try:
        return dict(st.secrets["accounts"][account_key])
    except Exception:
        st.error(f"No encontré credenciales en st.secrets['accounts']['{account_key}'].")
        st.stop()  # corta la app


def creds_to_dict(creds: Credentials) -> dict:
    return {
        "token": creds.token,
        "refresh_token": getattr(creds, "refresh_token", None),
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
    }


# =============================
# Flujo "Installed app" (copy/paste) — usado SOLO para pasos 1 y 2
# =============================
def build_flow(account_key: str, scopes: List[str]) -> Flow:
    """
    Crea un flujo OAuth2 tipo 'installed' (redirect http://localhost).
    Esto se usa para PASO 1 (Drive/Sheets) y PASO 2 (GSC) en modo manual.
    """
    acc = _get_account(account_key)
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
# Flujo "Web" (redirect a la app) — usado para PASO 0 con BOTÓN
# =============================
def _discover_web_keys(acc: Dict[str, str]) -> Tuple[Optional[str], Optional[str]]:
    """
    Acepta varias convenciones de keys para el cliente 'web'.
    """
    cid = acc.get("web_client_id") or acc.get("client_id_web") or acc.get("client_id_webapp")
    csec = acc.get("web_client_secret") or acc.get("client_secret_web") or acc.get("client_secret_webapp")
    return cid, csec


def is_redirect_ready(account_key: str) -> bool:
    acc = _get_account(account_key)
    redirect_uri = (st.secrets.get("auth", {}) or {}).get("redirect_uri") or acc.get("redirect_uri")
    cid, csec = _discover_web_keys(acc)
    return bool(redirect_uri and cid and csec)


def build_flow_web(account_key: str, scopes: List[str]) -> Flow:
    """
    Crea un flujo OAuth2 'web' que redirige de vuelta a esta app (sin copiar URL).
    Requiere:
      - accounts[account_key].web_client_id / web_client_secret  (o alias compatibles)
      - auth.redirect_uri (o accounts[account_key].redirect_uri)
    """
    acc = _get_account(account_key)
    redirect_uri = (st.secrets.get("auth", {}) or {}).get("redirect_uri") or acc.get("redirect_uri")
    cid, csec = _discover_web_keys(acc)

    if not (redirect_uri and cid and csec):
        raise RuntimeError(
            "Falta configurar el cliente OAuth Web o el redirect_uri. "
            "Agregá en secrets: [accounts.<key>.web_client_id, web_client_secret] y [auth.redirect_uri]."
        )

    client_secrets = {
        "web": {
            "client_id": cid,
            "client_secret": csec,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }
    flow = Flow.from_client_config(client_secrets, scopes=scopes)
    flow.redirect_uri = redirect_uri
    return flow


# =============================
# Cache de credenciales
# =============================
def get_cached_personal_creds() -> Optional[Credentials]:
    """
    Devuelve Credentials de la CUENTA PERSONAL (Drive/Sheets) si está cacheado.
    """
    creds = token_store.as_credentials("creds_dest")
    if creds:
        return creds

    data = token_store.load("creds_dest")
    if not data:
        data = st.session_state.get("creds_dest")
    if data:
        try:
            return Credentials(**data)
        except Exception as e:
            debug_log("[get_cached_personal_creds] no pude construir Credentials desde dict", str(e))
    return None


# =============================
# OAuth PERSONAL (Drive/Sheets) — Paso 1 (modo manual)
# =============================
def pick_destination_oauth():
    st.subheader("1) Conectar Google PERSONAL (Drive/Sheets)")

    acct_for_dest = st.secrets.get("oauth_app_key", "ACCESO")

    if st.session_state.get("oauth_dest", {}).get("account_key") != acct_for_dest:
        st.session_state.pop("oauth_dest", None)

    if "oauth_dest" not in st.session_state:
        from .utils import build_flow_drive  # helper con scopes de Drive/Sheets
        flow = build_flow_drive(acct_for_dest)
        auth_url, state = flow.authorization_url(
            prompt="consent select_account",
            access_type="offline",
        )
        st.session_state["oauth_dest"] = {
            "account_key": acct_for_dest,
            "flow": flow,
            "auth_url": auth_url,
            "state": state,
        }

    od = st.session_state["oauth_dest"]
    st.markdown(f"🔗 **Paso A (personal):** [Autorizar Drive/Sheets]({od['auth_url']})")
    with st.expander("Ver/copiar URL de autorización (personal)"):
        st.code(od["auth_url"])

    url = st.text_input(
        "🔑 Paso B (personal): pegá la URL completa (http://localhost/?code=...&state=...)",
        key="auth_response_url_dest",
        placeholder="http://localhost/?code=...&state=...",
    )

    col1, col2 = st.columns([1, 1])
    creds = None

    with col1:
        if st.button("Conectar Google PERSONAL", type="primary", key="btn_connect_dest"):
            if not url.strip():
                st.error("Pegá la URL completa de redirección (incluye code y state).")
                st.stop()
            try:
                qs = parse_qs(urlsplit(url.strip()).query)
                returned_state = (qs.get("state") or [""])[0]
            except Exception:
                returned_state = ""

            expected_state = od.get("state")
            if not returned_state or returned_state != expected_state:
                st.error("CSRF Warning: el 'state' devuelto **no coincide** con el generado.")
                st.info("Hacé clic en **Reiniciar Paso 1** y repetí la autorización (un solo click).")
                st.stop()

            try:
                flow: Flow = od["flow"]
                flow.fetch_token(authorization_response=url.strip())
                creds = flow.credentials
                data = creds_to_dict(creds)
                st.session_state["creds_dest"] = data
                token_store.save("creds_dest", data)
                st.success("Cuenta PERSONAL conectada.")
            except Exception as e:
                st.error("No se pudo conectar la cuenta PERSONAL. Reintentá autorización y pegá la URL completa.")
                st.caption(f"Detalle técnico: {e}")
                st.stop()

    with col2:
        if st.button("Reiniciar Paso 1", key="btn_reset_dest"):
            st.session_state.pop("oauth_dest", None)
            st.session_state.pop("creds_dest", None)
            token_store.clear("creds_dest")
            st.success("Restaurado. Volvé a hacer clic en 'Autorizar Drive/Sheets'.")
            st.stop()

    if not creds and st.session_state.get("creds_dest"):
        try:
            creds = Credentials(**st.session_state["creds_dest"])
        except Exception:
            creds = token_store.as_credentials("creds_dest")

    return creds


# =============================
# OAuth FUENTE (Search Console) — Paso 2 (manual)
# =============================
def pick_source_oauth() -> Optional[Credentials]:
    st.subheader("2) Conectar cuenta de Search Console (fuente de datos)")

    acct = st.radio(
        "Cuenta SC:",
        options=["ACCESO", "ACCESO_MEDIOS"],
        captions=["Usar client_id de Acceso", "Usar client_id de Acceso Medios"],
        horizontal=True,
        key="acct_choice_sc",
    )

    if st.session_state.get("oauth_src", {}).get("account") != acct:
        st.session_state.pop("oauth_src", None)

    if "oauth_src" not in st.session_state:
        flow = build_flow(acct, SCOPES_GSC)
        auth_url, state = flow.authorization_url(
            prompt="consent select_account",
            access_type="offline",
        )
        st.session_state["oauth_src"] = {
            "account": acct,
            "flow": flow,
            "auth_url": auth_url,
            "state": state,
        }

    osrc = st.session_state["oauth_src"]
    st.markdown(f"🔗 **Paso A (fuente):** [Autorizar acceso a Search Console]({osrc['auth_url']})")
    with st.expander("Ver/copiar URL de autorización (fuente)"):
        st.code(osrc["auth_url"])

    url = st.text_input(
        "🔑 Paso B (fuente): pegá la URL completa (http://localhost/?code=...&state=...)",
        key="auth_response_url_src",
        placeholder="http://localhost/?code=...&state=...",
    )

    col1, col2 = st.columns([1, 1])
    creds = None

    with col1:
        if st.button("Conectar Search Console", type="secondary"):
            if not url.strip():
                st.error("Pegá la URL completa de redirección (incluye code y state).")
                st.stop()

            try:
                qs = parse_qs(urlsplit(url.strip()).query)
                returned_state = (qs.get("state") or [""])[0]
            except Exception:
                returned_state = ""

            expected_state = osrc.get("state")
            if not returned_state or returned_state != expected_state:
                st.error("CSRF Warning: el 'state' devuelto **no coincide** con el generado.")
                st.info("Hacé clic en **Reiniciar Paso 2** y repetí la autorización (un solo click).")
                st.stop()

            try:
                flow: Flow = osrc["flow"]
                flow.fetch_token(authorization_response=url.strip())
                creds = flow.credentials
                data = creds_to_dict(creds)
                token_store.save("creds_src", data)
                st.success("Cuenta SC conectada.")
            except Exception as e:
                st.error("No se pudo conectar Search Console. Reintentá autorización y pegá la URL completa.")
                st.caption(f"Detalle técnico: {e}")

    with col2:
        if st.button("Reiniciar Paso 2", key="btn_reset_src"):
            st.session_state.pop("oauth_src", None)
            token_store.clear("creds_src")
            st.success("Restaurado. Volvé a hacer clic en 'Autorizar acceso a Search Console'.")
            st.stop()

    if not creds:
        creds_dict = token_store.load("creds_src")
        if creds_dict:
            try:
                creds = Credentials(**creds_dict)
            except Exception as e:
                debug_log("[pick_source_oauth] no pude construir Credentials desde dict", str(e))
                creds = None

    return creds