# modules/auth.py
from __future__ import annotations

import streamlit as st
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials

from .utils import debug_log, token_store


# =============================
# Scopes
# =============================
SCOPES_DRIVE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
SCOPES_GSC = [
    "https://www.googleapis.com/auth/webmasters.readonly",
]


# =============================
# Helpers internos
# =============================
def _account_config(account_key: str) -> dict:
    """Lee client_id/client_secret de st.secrets['accounts'][account_key]."""
    try:
        acc = st.secrets["accounts"][account_key]
        if not acc.get("client_id") or not acc.get("client_secret"):
            raise KeyError("Falta client_id/client_secret")
        return acc
    except Exception:
        st.error(
            f"No encontré credenciales en st.secrets['accounts']['{account_key}'].\n"
            "Agregá client_id y client_secret."
        )
        st.stop()


def _build_flow(account_key: str, scopes: list[str]) -> Flow:
    acc = _account_config(account_key)
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


def _creds_to_dict(creds: Credentials) -> dict:
    return {
        "token": creds.token,
        "refresh_token": getattr(creds, "refresh_token", None),
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes) if getattr(creds, "scopes", None) else None,
    }


# =============================
# API pública
# =============================
def get_cached_personal_creds() -> Credentials | None:
    """Devuelve credenciales personales (Drive/Sheets) desde cache si existen."""
    data = token_store.get("creds_dest")
    if not data:
        return None
    try:
        return Credentials(**data)
    except Exception as e:
        debug_log("No pude reconstruir creds_dest desde cache", str(e))
        token_store.clear("creds_dest")
        return None


def pick_destination_oauth() -> Credentials | None:
    """
    OAuth para la cuenta PERSONAL (Drive/Sheets).
    - Si ya está cacheado en token_store, muestra estado y devuelve las credenciales.
    - Si no, guía de autorización con URL + pega la redirect URL.
    """
    st.subheader("1) Conectar Google PERSONAL (Drive/Sheets)")

    # ¿Ya tenemos credenciales cacheadas?
    cached = get_cached_personal_creds()
    if cached:
        st.success("Cuenta PERSONAL conectada.")
        col1, col2 = st.columns([1, 2])
        with col1:
            if st.button("Cambiar de cuenta (Drive/Sheets)"):
                token_store.clear("creds_dest")
                st.session_state.pop("oauth_dest", None)
                st.experimental_rerun()
        with col2:
            st.caption("Tus archivos se crearán en el Drive de esta cuenta.")
        return cached

    # Si no hay cache, armo el flow y muestro pasos
    acct_for_dest = st.secrets.get("oauth_app_key", "ACCESO")
    # Reset si cambia la app key
    if st.session_state.get("oauth_dest", {}).get("account_key") != acct_for_dest:
        st.session_state.pop("oauth_dest", None)

    if "oauth_dest" not in st.session_state:
        flow = _build_flow(acct_for_dest, SCOPES_DRIVE)
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

    if st.button("Conectar Google PERSONAL", type="primary"):
        if not url.strip():
            st.error("Pegá la URL completa de redirección (incluye code y state).")
            st.stop()
        try:
            flow: Flow = od["flow"]
            flow.fetch_token(authorization_response=url.strip())
            creds = flow.credentials
            token_store.set("creds_dest", _creds_to_dict(creds))
            st.session_state.pop("oauth_dest", None)
            st.success("Cuenta PERSONAL conectada.")
            return creds
        except Exception as e:
            st.error("No se pudo conectar la cuenta PERSONAL. Reintentá autorización y pegá la URL completa.")
            st.caption(f"Detalle técnico (debug): {e}")
            # Reiniciar flujo para regenerar auth_url/state
            st.session_state.pop("oauth_dest", None)

    return None


def pick_source_oauth() -> Credentials | None:
    """
    OAuth para la cuenta de Search Console (fuente: ACCESO o ACCESO_MEDIOS).
    - Cache por cuenta: guarda bajo clave 'creds_src:<acct>'.
    - Si ya está, muestra estado y permite “Cambiar de cuenta”.
    """
    st.subheader("2) Conectar cuenta de Search Console (fuente de datos)")
    acct = st.radio(
        "Cuenta SC:",
        options=["ACCESO", "ACCESO_MEDIOS"],
        captions=["Usar client_id de Acceso", "Usar client_id de Acceso Medios"],
        horizontal=True,
        key="acct_choice_sc",
    )

    cache_key = f"creds_src:{acct}"
    cached = token_store.get(cache_key)
    if cached:
        try:
            creds = Credentials(**cached)
            st.success(f"Cuenta SC conectada ({acct}).")
            col1, col2 = st.columns([1, 2])
            with col1:
                if st.button("Cambiar de cuenta (SC)"):
                    token_store.clear(cache_key)
                    st.session_state.pop("oauth_src", None)
                    st.experimental_rerun()
            with col2:
                st.caption("Usaremos esta cuenta para leer datos de Search Console.")
            return creds
        except Exception as e:
            debug_log("No pude reconstruir creds_src desde cache", str(e))
            token_store.clear(cache_key)

    # Reset si cambia la cuenta elegida
    if st.session_state.get("oauth_src", {}).get("account") != acct:
        st.session_state.pop("oauth_src", None)

    if "oauth_src" not in st.session_state:
        flow = _build_flow(acct, SCOPES_GSC)
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

    if st.button("Conectar Search Console", type="secondary"):
        if not url.strip():
            st.error("Pegá la URL completa de redirección (incluye code y state).")
            st.stop()
        try:
            flow: Flow = osrc["flow"]
            flow.fetch_token(authorization_response=url.strip())
            creds = flow.credentials
            token_store.set(cache_key, _creds_to_dict(creds))
            st.session_state.pop("oauth_src", None)
            st.success(f"Cuenta SC conectada ({acct}).")
            return creds
        except Exception as e:
            st.error("No se pudo conectar Search Console. Reintentá autorización y pegá la URL completa.")
            st.caption(f"Detalle técnico (debug): {e}")
            st.session_state.pop("oauth_src", None)

    return None
