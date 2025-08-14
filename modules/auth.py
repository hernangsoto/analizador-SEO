# modules/auth.py
from __future__ import annotations

import streamlit as st
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

from .utils import debug_log, token_store

# Scopes
SCOPES_DRIVE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
SCOPES_GSC = ["https://www.googleapis.com/auth/webmasters.readonly"]


def build_flow(account_key: str, scopes: list[str]) -> Flow:
    """Crea un flujo OAuth2 a partir de st.secrets['accounts'][account_key]."""
    try:
        acc = st.secrets["accounts"][account_key]
    except Exception:
        st.error("No encontré las credenciales en st.secrets['accounts'][…].")
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
        "scopes": list(creds.scopes) if getattr(creds, "scopes", None) else SCOPES_DRIVE,
        "expiry": getattr(creds, "expiry", None),
    }


def refresh_if_needed(creds: Credentials) -> Credentials:
    try:
        if creds and not creds.valid and getattr(creds, "refresh_token", None):
            creds.refresh(Request())
    except Exception as e:
        debug_log("No se pudo refrescar token", str(e))
    return creds


def _streamlit_email() -> str | None:
    u = getattr(st, "user", getattr(st, "experimental_user", None))
    return getattr(u, "email", None)


# ==========
# NUEVO: Recupera credenciales personales sin UI si ya autorizaste antes
# ==========
def get_cached_personal_creds() -> Credentials | None:
    """
    Devuelve credenciales de Drive/Sheets para la cuenta personal SIN mostrar UI,
    reutilizando token del store de servidor o de la sesión, y refrescándolo si hace falta.
    """
    user_email = _streamlit_email()
    cache = st.session_state.setdefault("creds_cache", {"dest": {}, "src": {}})
    store = token_store()

    creds = None

    # 1) Store (servidor)
    if user_email and store["dest"].get(user_email):
        creds = Credentials(**store["dest"][user_email])
        try:
            if not creds.valid and getattr(creds, "refresh_token", None):
                creds.refresh(Request())
        except Exception as e:
            debug_log("No se pudo refrescar token (store dest)", str(e))

    # 2) Cache de sesión (si el store no sirvió)
    if not (creds and creds.valid) and user_email and cache["dest"].get(user_email):
        creds = Credentials(**cache["dest"][user_email])
        try:
            if not creds.valid and getattr(creds, "refresh_token", None):
                creds.refresh(Request())
        except Exception as e:
            debug_log("No se pudo refrescar token (session dest)", str(e))

    if creds and creds.valid:
        st.session_state["creds_dest"] = creds_to_dict(creds)
        return creds
    return None


def pick_destination_oauth() -> Credentials | None:
    """OAuth para la cuenta PERSONAL (Drive/Sheets). Guarda token en store+sesión."""
    st.subheader("1) Conectar Google PERSONAL (Drive/Sheets)")

    user_email = _streamlit_email()
    cache = st.session_state.setdefault("creds_cache", {"dest": {}, "src": {}})
    store = token_store()

    # Si ya hay token en caché o store, saltamos UI (por si llamaron directo)
    if user_email and (store["dest"].get(user_email) or cache["dest"].get(user_email)):
        creds = get_cached_personal_creds()
        if creds and creds.valid:
            st.success(f"Cuenta PERSONAL conectada (automática): {user_email}")
            if st.button("Cambiar cuenta PERSONAL"):
                try:
                    store["dest"].pop(user_email, None)
                    cache["dest"].pop(user_email, None)
                except Exception:
                    pass
                st.session_state.pop("creds_dest", None)
                st.session_state.pop("oauth_dest", None)
                st.experimental_rerun()
            return creds

    # Flujo nuevo
    acct_for_dest = st.secrets.get("oauth_app_key", "ACCESO")
    if st.session_state.get("oauth_dest", {}).get("account_key") != acct_for_dest:
        st.session_state.pop("oauth_dest", None)

    if "oauth_dest" not in st.session_state:
        flow = build_flow(acct_for_dest, SCOPES_DRIVE)
        auth_url, state = flow.authorization_url(
            prompt="select_account",
            access_type="offline",
            include_granted_scopes=False,
        )
        st.session_state["oauth_dest"] = {
            "account_key": acct_for_dest,
            "flow": flow,
            "auth_url": auth_url,
            "state": state,
        }

    od = st.session_state["oauth_dest"]
    st.markdown(f"🔗 **Paso A (personal):** [Autorizar acceso de Drive/Sheets]({od['auth_url']})")
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
            st.session_state["creds_dest"] = creds_to_dict(creds)
            if user_email:
                cache["dest"][user_email] = st.session_state["creds_dest"]
                store["dest"][user_email] = st.session_state["creds_dest"]
            st.success("Cuenta PERSONAL conectada.")
        except Exception as e:
            st.session_state.pop("oauth_dest", None)
            st.error("No se pudo conectar la cuenta PERSONAL. Reintentá autorización y pegá la URL completa.")
            st.caption(f"Detalle técnico (debug): {e}")

    if not creds and st.session_state.get("creds_dest"):
        creds = Credentials(**st.session_state["creds_dest"])
        creds = refresh_if_needed(creds)
    return creds


def pick_source_oauth() -> Credentials | None:
    """OAuth para Search Console (ACCESO o ACCESO_MEDIOS). Guarda token en store+sesión."""
    st.subheader("2) Conectar cuenta de Search Console (fuente de datos)")
    acct = st.radio(
        "Cuenta SC:",
        options=["ACCESO", "ACCESO_MEDIOS"],
        captions=["Usar client_id de Acceso", "Usar client_id de Acceso Medios"],
        horizontal=True,
        key="acct_choice_sc",
    )

    cache = st.session_state.setdefault("creds_cache", {"dest": {}, "src": {}})
    store = token_store()

    # Intentar store
    if store["src"].get(acct):
        creds = Credentials(**store["src"][acct])
        creds = refresh_if_needed(creds)
        if creds and creds.valid:
            st.success(f"Search Console conectado (automático): {acct}")
            if st.button("Cambiar cuenta SC"):
                store["src"].pop(acct, None)
                cache["src"].pop(acct, None)
                st.session_state.pop("creds_src", None)
                st.session_state.pop("oauth_src", None)
                st.experimental_rerun()
            st.session_state["creds_src"] = creds_to_dict(creds)
            return creds

    # Intentar caché de sesión
    if cache["src"].get(acct):
        creds = Credentials(**cache["src"][acct])
        creds = refresh_if_needed(creds)
        if creds and creds.valid:
            st.success(f"Search Console conectado (caché de sesión): {acct}")
            if st.button("Cambiar cuenta SC"):
                cache["src"].pop(acct, None)
                st.session_state.pop("creds_src", None)
                st.session_state.pop("oauth_src", None)
                st.experimental_rerun()
            st.session_state["creds_src"] = creds_to_dict(creds)
            return creds

    # Flujo nuevo
    if st.session_state.get("oauth_src", {}).get("account") != acct:
        st.session_state.pop("oauth_src", None)

    if "oauth_src" not in st.session_state:
        flow = build_flow(acct, SCOPES_GSC)
        auth_url, state = flow.authorization_url(
            prompt="select_account",
            access_type="offline",
            include_granted_scopes=False,
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
            st.session_state["creds_src"] = creds_to_dict(creds)
            store["src"][acct] = st.session_state["creds_src"]
            cache["src"][acct] = st.session_state["creds_src"]
            st.success("Cuenta SC conectada.")
        except Exception as e:
            st.session_state.pop("oauth_src", None)
            st.error("No se pudo conectar Search Console. Reintentá autorización y pegá la URL completa.")
            st.caption(f"Detalle técnico (debug): {e}")

    if not creds and st.session_state.get("creds_src"):
        creds = Credentials(**st.session_state["creds_src"])
        creds = refresh_if_needed(creds)
    return creds
