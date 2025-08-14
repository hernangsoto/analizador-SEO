# modules/auth.py
from __future__ import annotations

from typing import Optional, Tuple, List

import streamlit as st
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials

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


# =============================
# Helpers
# =============================
def build_flow(account_key: str, scopes: List[str]) -> Flow:
    """
    Crea un flujo OAuth2 a partir de st.secrets['accounts'][account_key] con los scopes provistos.
    Usa redirect http://localhost (compatible con pegar la URL de redirección en Streamlit).
    """
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
# Cache de credenciales
# =============================
def get_cached_personal_creds() -> Optional[Credentials]:
    """
    Devuelve Credentials de la CUENTA PERSONAL (Drive/Sheets) si está cacheado.
    Fix: usar token_store.load / as_credentials (no .get()).
    """
    # Preferir construir Credentials directamente desde el token_store
    creds = token_store.as_credentials("creds_dest")
    if creds:
        return creds

    # Fallback por compatibilidad: leer dict crudo si existe en session_state
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
# OAuth PERSONAL (Drive/Sheets)
# =============================
def pick_destination_oauth() -> Optional[Credentials]:
    """
    Autentica la cuenta PERSONAL (donde se crearán/copiarán los Sheets).
    - Si hay cache (token_store) devuelve directo.
    - Si no, muestra el flujo de autorización con 'pegar URL'.
    """
    # 1) Cache
    cached = get_cached_personal_creds()
    if cached:
        st.caption("🔐 Cuenta PERSONAL ya conectada (desde caché).")
        return cached

    # 2) Flujo de autorización
    st.subheader("1) Conectar Google PERSONAL (Drive/Sheets)")
    acct_for_dest = st.secrets.get("oauth_app_key", "ACCESO")  # permite elegir qué client_id usar
    # Reset si cambia la app key
    if st.session_state.get("oauth_dest", {}).get("account_key") != acct_for_dest:
        st.session_state.pop("oauth_dest", None)

    if "oauth_dest" not in st.session_state:
        flow = build_flow(acct_for_dest, SCOPES_DRIVE)
        auth_url, state = flow.authorization_url(
            prompt="consent select_account",
            access_type="offline",
            include_granted_scopes="false",
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

    creds = None
    if st.button("Conectar Google PERSONAL", type="primary"):
        if not url.strip():
            st.error("Pegá la URL completa de redirección (incluye code y state).")
            st.stop()
        try:
            flow: Flow = od["flow"]
            flow.fetch_token(authorization_response=url.strip())
            creds = flow.credentials
            token_store.save("creds_dest", creds_to_dict(creds))
            st.success("Cuenta PERSONAL conectada.")
        except Exception as e:
            # invalidar flujo guardado para regenerar auth_url en el próximo intento
            st.session_state.pop("oauth_dest", None)
            st.error("No se pudo conectar la cuenta PERSONAL. Reintentá autorización y pegá la URL completa.")
            st.caption(f"Detalle técnico: {e}")

    # Leer de cache si ya guardamos en este mismo run
    if not creds:
        creds = get_cached_personal_creds()

    return creds


# =============================
# OAuth FUENTE (Search Console)
# =============================
def pick_source_oauth() -> Optional[Credentials]:
    """
    Autentica la cuenta FUENTE para Search Console (ACCESO o ACCESO_MEDIOS).
    """
    st.subheader("2) Conectar cuenta de Search Console (fuente de datos)")

    acct = st.radio(
        "Cuenta SC:",
        options=["ACCESO", "ACCESO_MEDIOS"],
        captions=["Usar client_id de Acceso", "Usar client_id de Acceso Medios"],
        horizontal=True,
        key="acct_choice_sc",
    )

    # Reset si cambia la cuenta
    if st.session_state.get("oauth_src", {}).get("account") != acct:
        st.session_state.pop("oauth_src", None)

    if "oauth_src" not in st.session_state:
        flow = build_flow(acct, SCOPES_GSC)
        auth_url, state = flow.authorization_url(
            prompt="consent select_account",
            access_type="offline",
            include_granted_scopes="false",
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

    creds = None
    if st.button("Conectar Search Console", type="secondary"):
        if not url.strip():
            st.error("Pegá la URL completa de redirección (incluye code y state).")
            st.stop()
        try:
            flow: Flow = osrc["flow"]
            flow.fetch_token(authorization_response=url.strip())
            creds = flow.credentials
            token_store.save("creds_src", creds_to_dict(creds))
            st.success("Cuenta SC conectada.")
        except Exception as e:
            st.session_state.pop("oauth_src", None)
            st.error("No se pudo conectar Search Console. Reintentá autorización y pegá la URL completa.")
            st.caption(f"Detalle técnico: {e}")

    # Leer de cache si ya guardamos en este mismo run
    if not creds:
        creds_dict = token_store.load("creds_src")
        if creds_dict:
            try:
                creds = Credentials(**creds_dict)
            except Exception as e:
                debug_log("[pick_source_oauth] no pude construir Credentials desde dict", str(e))
                creds = None

    return creds
