import time
import json
import requests
import streamlit as st
from authlib.integrations.requests_client import OAuth2Session

st.set_page_config(page_title="Login con Google", page_icon="🔐")

# --- Config ---
CLIENT_ID = st.secrets["google"]["client_id"]
CLIENT_SECRET = st.secrets["google"]["client_secret"]
APP_URL = st.secrets["google"]["app_url"].rstrip("/")  # p.ej. "http://localhost:8501"

# Scopes típicos (incluye openid para obtener ID token, email y perfil)
SCOPES = [
    "openid",
    "email",
    "profile",
]

# Endpoints de Google
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

# Redirect URI (debe coincidir con Google Cloud)
REDIRECT_URI = APP_URL  # o f"{APP_URL}/oauth2/callback" si prefieres una ruta dedicada

# --- Estado inicial ---
if "token" not in st.session_state:
    st.session_state.token = None
if "user" not in st.session_state:
    st.session_state.user = None

def build_oauth_session(state=None, code_verifier=None):
    return OAuth2Session(
        CLIENT_ID,
        CLIENT_SECRET,
        scope=SCOPES,
        redirect_uri=REDIRECT_URI,
        # Se puede reforzar PKCE (Authlib genera/verifica si se usa create_authorization_url con code_challenge_method)
    )

def login():
    oauth = build_oauth_session()
    # params extra para refresh_token: access_type=offline + prompt=consent
    uri, state = oauth.create_authorization_url(
        AUTH_URL,
        access_type="offline",
        prompt="consent",
        include_granted_scopes="true",
    )
    st.session_state.oauth_state = state
    st.experimental_set_query_params()  # limpia cualquier query previa
    st.markdown(f"[Haz clic para continuar con Google]({uri})")

def fetch_userinfo(access_token: str):
    r = requests.get(
        USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()

def handle_callback():
    # Lee parámetros del callback (p.ej. ?code=...&state=...)
    params = st.experimental_get_query_params()
    if "code" not in params:
        return

    code = params["code"][0]
    state = params.get("state", [None])[0]

    # Validación de state (opcional/fácil)
    expected_state = st.session_state.get("oauth_state")
    if expected_state and state != expected_state:
        st.error("Estado de OAuth no coincide. Intenta de nuevo.")
        return

    # Intercambio de código por tokens
    oauth = build_oauth_session()
    token = oauth.fetch_token(
        TOKEN_URL,
        code=code,
        grant_type="authorization_code",
    )

    st.session_state.token = token

    # Obtiene datos del usuario
    user = fetch_userinfo(token["access_token"])
    st.session_state.user = user

    # Limpia el querystring para no dejar el código en la URL
    st.experimental_set_query_params()

def maybe_refresh_token():
    """Refresca el access_token si expira y hay refresh_token."""
    tok = st.session_state.token
    if not tok:
        return

    expires_at = tok.get("expires_at")
    if not expires_at:
        return

    # Si faltan <60s para expirar, refrescar
    if time.time() > (expires_at - 60):
        refresh_token = tok.get("refresh_token")
        if not refresh_token:
            return
        data = {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
        r = requests.post(TOKEN_URL, data=data, timeout=10)
        if r.ok:
            new_tok = r.json()
            # Mantén el refresh_token viejo si Google no devuelve uno nuevo
            if "refresh_token" not in new_tok and "refresh_token" in tok:
                new_tok["refresh_token"] = tok["refresh_token"]
            st.session_state.token = new_tok

def logout():
    st.session_state.token = None
    st.session_state._
