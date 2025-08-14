import streamlit as st
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from modules.utils import debug_log

SCOPES_DRIVE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
SCOPES_GSC = ["https://www.googleapis.com/auth/webmasters.readonly"]

def build_flow(account_key: str, scopes: list[str]) -> Flow:
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

def creds_to_dict(creds: Credentials):
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

def _streamlit_email():
    u = getattr(st, "user", getattr(st, "experimental_user", None))
    return getattr(u, "email", None)

def pick_destination_oauth():
    """OAuth para la cuenta PERSONAL (Drive/Sheets). Reutiliza tokens si ya existen en caché."""
    st.subheader("1) Conectar Google PERSONAL (Drive/Sheets)")

    user_email = _streamlit_email()
    cache = st.session_state.setdefault("creds_cache", {"dest": {}, "src": {}})

    # 1) Intentar usar caché por email de Streamlit
    if user_email and cache["dest"].get(user_email):
        cached = cache["dest"][user_email]
        creds = Credentials(**cached)
        creds = refresh_if_needed(creds)
        if creds and creds.valid:
            st.success(f"Cuenta PERSONAL conectada (caché): {user_email}")
            if st.button("Cambiar cuenta PERSONAL"):
                cache["dest"].pop(user_email, None)
                st.session_state.pop("creds_dest", None)
                st.session_state.pop("oauth_dest", None)
                st.experimental_rerun()
            st.session_state["creds_dest"] = creds_to_dict(creds)
            return creds

    # 2) Preparar flujo OAuth
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
            st.success("Cuenta PERSONAL conectada.")
        except Exception as e:
            st.session_state.pop("oauth_dest", None)
            st.error("No se pudo conectar la cuenta PERSONAL. Reintentá autorización y pegá la URL completa.")
            st.caption(f"Detalle técnico (debug): {e}")

    if not creds and st.session_state.get("creds_dest"):
        creds = Credentials(**st.session_state["creds_dest"])
        creds = refresh_if_needed(creds)
    return creds

def pick_source_oauth():
    """OAuth para la cuenta de Search Console (fuente: ACCESO o ACCESO_MEDIOS). Reutiliza tokens por cuenta."""
    st.subheader("2) Conectar cuenta de Search Console (fuente de datos)")
    acct = st.radio(
        "Cuenta SC:",
        options=["ACCESO", "ACCESO_MEDIOS"],
        captions=["Usar client_id de Acceso", "Usar client_id de Acceso Medios"],
        horizontal=True,
        key="acct_choice_sc",
    )

    cache = st.session_state.setdefault("creds_cache", {"dest": {}, "src": {}})

    # 1) Caché por cuenta SC
    if cache["src"].get(acct):
        cached = cache["src"][acct]
        creds = Credentials(**cached)
        creds = refresh_if_needed(creds)
        if creds and creds.valid:
            st.success(f"Search Console conectado (caché): {acct}")
            if st.button("Cambiar cuenta SC"):
                cache["src"].pop(acct, None)
                st.session_state.pop("creds_src", None)
                st.session_state.pop("oauth_src", None)
                st.experimental_rerun()
            st.session_state["creds_src"] = creds_to_dict(creds)
            return creds

    # 2) Flujo OAuth nuevo
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

