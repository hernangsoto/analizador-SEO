# app.py
import streamlit as st
import time
import base64
import hashlib
import os
import json
import requests
from urllib.parse import urlencode, urlunparse

st.set_page_config(page_title="Login con Google", page_icon="🔐")

# ========= Config =========
CLIENT_ID = st.secrets.get("client_id", "828947627584-2gb6qjlqtci802716dhbk1aa499nbom9.apps.googleusercontent.com")
CLIENT_SECRET = st.secrets.get("client_secret", "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")

# URL de tu app en Streamlit (con barra final)
PRODUCTION_REDIRECT_URI = "https://hernangsoto.streamlit.app/"
LOCAL_REDIRECT_URI = "http://localhost:8501/"
REDIRECT_URI = PRODUCTION_REDIRECT_URI  # Cambia a LOCAL_REDIRECT_URI para pruebas locales

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
SCOPES = ["openid", "email", "profile"]

# ========= Helpers =========
def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

def new_code_verifier() -> str:
    return b64url(os.urandom(64))

def code_challenge_from(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return b64url(digest)

def current_url_base() -> str:
    # Devuelve la base de la URL actual, por si querés detectarla dinámicamente
    # Nota: en Streamlit Cloud suele ser tu dominio de la app
    # Usamos el REDIRECT_URI fijo para evitar sorpresas.
    return REDIRECT_URI

def get_query_params():
    try:
        return st.query_params  # Streamlit >= 1.30
    except Exception:
        return st.experimental_get_query_params()  # fallback

def set_query_params(**kwargs):
    try:
        st.query_params.clear()
        for k, v in kwargs.items():
            st.query_params[k] = v
    except Exception:
        st.experimental_set_query_params(**kwargs)

def start_oauth_flow():
    # Genera state, PKCE y arma URL de autorización
    verifier = new_code_verifier()
    challenge = code_challenge_from(verifier)
    state = b64url(os.urandom(32))

    st.session_state["oauth_verifier"] = verifier
    st.session_state["oauth_state"] = state
    st.session_state["redirect_uri"] = current_url_base()

    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "redirect_uri": st.session_state["redirect_uri"],
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
    }
    return f"{AUTH_URL}?{urlencode(params)}"

def exchange_code_for_tokens(auth_code: str, state: str):
    # Valida state y canjea code por tokens
    if "oauth_state" not in st.session_state or state != st.session_state["oauth_state"]:
        raise RuntimeError("State inválido o sesión expirada. Volvé a iniciar sesión.")

    verifier = st.session_state.get("oauth_verifier")
    if not verifier:
        raise RuntimeError("Falta PKCE verifier en la sesión. Volvé a iniciar sesión.")

    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": auth_code,
        "grant_type": "authorization_code",
        "redirect_uri": st.session_state.get("redirect_uri", REDIRECT_URI),
        "code_verifier": verifier,
    }
    resp = requests.post(TOKEN_URL, data=data, timeout=20)
    if resp.status_code != 200:
        raise RuntimeError(f"Error al obtener tokens: {resp.text}")
    return resp.json()

def fetch_userinfo(access_token: str):
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = requests.get(USERINFO_URL, headers=headers, timeout=20)
    if resp.status_code != 200:
        raise RuntimeError(f"Error al traer userinfo: {resp.text}")
    return resp.json()

def logout():
    for k in [
        "tokens", "user", "oauth_verifier", "oauth_state", "redirect_uri", "nombre"
    ]:
        if k in st.session_state:
            del st.session_state[k]
    # Limpia query params (por si quedó ?code=...)
    set_query_params()
    st.success("Sesión cerrada.")
    st.rerun()

# ========= UI =========
st.title("🔐 Login con Google (Streamlit)")
st.caption("Primero ingresá tu nombre, después hacé Sign in with Google.")

# 1) Pedir nombre
nombre = st.text_input("Tu nombre", value=st.session_state.get("nombre", ""))
if nombre:
    st.session_state["nombre"] = nombre

# 2) Manejo de retorno OAuth (code + state)
qp = get_query_params()
auth_code = None
auth_state = None
# soporta listas (experimental_get_query_params) o valores simples (st.query_params)
if qp:
    code_val = qp.get("code")
    state_val = qp.get("state")
    if isinstance(code_val, list):
        auth_code = code_val[0] if code_val else None
    else:
        auth_code = code_val
    if isinstance(state_val, list):
        auth_state = state_val[0] if state_val else None
    else:
        auth_state = state_val

if auth_code and auth_state and "tokens" not in st.session_state:
    with st.spinner("Intercambiando código por tokens..."):
        try:
            tokens = exchange_code_for_tokens(auth_code, auth_state)
            st.session_state["tokens"] = tokens
            # Limpia code/state de la URL para que no molesten en recargas
            set_query_params()
            st.rerun()
        except Exception as e:
            st.error(str(e))

# 3) Estado autenticado
if "tokens" in st.session_state:
    tokens = st.session_state["tokens"]
    access_token = tokens.get("access_token")
    try:
        userinfo = fetch_userinfo(access_token)
        st.session_state["user"] = userinfo
    except Exception as e:
        st.error(str(e))

    user = st.session_state.get("user", {})
    st.success(f"¡Hola {st.session_state.get('nombre','')}! Te logueaste como {user.get('email','(email desconocido)')}")
    col1, col2 = st.columns([1, 3])
    with col1:
        if user.get("picture"):
            st.image(user["picture"], caption=user.get("name", ""))
    with col2:
        st.write("**Datos de la cuenta:**")
        st.json(
            {
                "name": user.get("name"),
                "email": user.get("email"),
                "given_name": user.get("given_name"),
                "family_name": user.get("family_name"),
                "sub": user.get("sub"),
                "hd": user.get("hd"),
            }
        )
        st.write("**Tokens (parcial):**")
        st.json(
            {
                "expires_in": tokens.get("expires_in"),
                "token_type": tokens.get("token_type"),
                "id_token_present": bool(tokens.get("id_token")),
                "refresh_token_present": bool(tokens.get("refresh_token")),
            }
        )

    st.button("Cerrar sesión", on_click=logout)

# 4) Estado no autenticado
else:
    if not nombre:
        st.info("Ingresá tu nombre para continuar.")
    else:
        st.write(f"¡Hola **{nombre}**! Ahora iniciá sesión con Google:")
        if st.button("🔓 Sign in with Google"):
            auth_link = start_oauth_flow()
            # Mostramos un botón de enlace para ir a Google
            st.link_button("Continuar con Google →", auth_link)
            st.stop()

    with st.expander("Detalles técnicos (ayuda)"):
        st.code(
            f"""
CLIENT_ID: {'(desde secrets)' if 'client_id' in st.secrets else CLIENT_ID}
REDIRECT_URI: {REDIRECT_URI}
SCOPES: {SCOPES}
            """.strip()
        )
