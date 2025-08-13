import streamlit as st
from google_auth_oauthlib.flow import Flow
import requests, urllib.parse, json

st.set_page_config(page_title="Prueba OAuth", page_icon="🔑")

def get_oauth_cfg():
    # 1) Intentar por secrets bien anidados
    try:
        return dict(st.secrets["gcp_oauth_client"]["web"])
    except Exception:
        pass
    # 2) Fallback: UI para pegar JSON del cliente (o escribir IDs)
    st.warning("No encontré [gcp_oauth_client] → [web] en secrets. Pegá los datos acá para probar.")
    col1, col2 = st.columns(2)
    with col1:
        client_id = st.text_input("client_id")
        client_secret = st.text_input("client_secret", type="password")
    with col2:
        redirect_uri = st.text_input("redirect_uri", value="https://hernangsoto.streamlit.app/")
    if client_id and client_secret:
        return {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    st.stop()

cfg = get_oauth_cfg()
CLIENT_ID = cfg["client_id"]
CLIENT_SECRET = cfg["client_secret"]
REDIRECT_URI = cfg["redirect_uris"][0]
AUTH_URI = cfg.get("auth_uri", "https://accounts.google.com/o/oauth2/auth")
TOKEN_URI = cfg.get("token_uri", "https://oauth2.googleapis.com/token")

# Paso 1: iniciar login
if "code" not in st.query_params:
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": "openid email profile",
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
    }
    auth_url = AUTH_URI + "?" + urllib.parse.urlencode(params)
    st.markdown(f"[🔑 Iniciar sesión con Google]({auth_url})")
else:
    # Paso 2: canjear código por tokens
    code = st.query_params["code"]
    data = {
        "code": code,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    }
    resp = requests.post(TOKEN_URI, data=data)
    token_info = resp.json()
    if "access_token" not in token_info:
        st.error("❌ Error al autenticar")
        st.json(token_info)
    else:
        st.success("✅ Login OK")
        # opcional: mostrar email
        me = requests.get(
            "https://www.googleapis.com/oauth2/v1/userinfo",
            params={"alt": "json", "access_token": token_info["access_token"]},
        ).json()
        st.write("Usuario:", me.get("email"))
import streamlit as st
from google_auth_oauthlib.flow import Flow
import requests, urllib.parse, json

st.set_page_config(page_title="Prueba OAuth", page_icon="🔑")

def get_oauth_cfg():
    # 1) Intentar por secrets bien anidados
    try:
        return dict(st.secrets["gcp_oauth_client"]["web"])
    except Exception:
        pass
    # 2) Fallback: UI para pegar JSON del cliente (o escribir IDs)
    st.warning("No encontré [gcp_oauth_client] → [web] en secrets. Pegá los datos acá para probar.")
    col1, col2 = st.columns(2)
    with col1:
        client_id = st.text_input("client_id")
        client_secret = st.text_input("client_secret", type="password")
    with col2:
        redirect_uri = st.text_input("redirect_uri", value="https://hernangsoto.streamlit.app/")
    if client_id and client_secret:
        return {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    st.stop()

cfg = get_oauth_cfg()
CLIENT_ID = cfg["client_id"]
CLIENT_SECRET = cfg["client_secret"]
REDIRECT_URI = cfg["redirect_uris"][0]
AUTH_URI = cfg.get("auth_uri", "https://accounts.google.com/o/oauth2/auth")
TOKEN_URI = cfg.get("token_uri", "https://oauth2.googleapis.com/token")

# Paso 1: iniciar login
if "code" not in st.query_params:
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": "openid email profile",
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
    }
    auth_url = AUTH_URI + "?" + urllib.parse.urlencode(params)
    st.markdown(f"[🔑 Iniciar sesión con Google]({auth_url})")
else:
    # Paso 2: canjear código por tokens
    code = st.query_params["code"]
    data = {
        "code": code,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    }
    resp = requests.post(TOKEN_URI, data=data)
    token_info = resp.json()
    if "access_token" not in token_info:
        st.error("❌ Error al autenticar")
        st.json(token_info)
    else:
        st.success("✅ Login OK")
        # opcional: mostrar email
        me = requests.get(
            "https://www.googleapis.com/oauth2/v1/userinfo",
            params={"alt": "json", "access_token": token_info["access_token"]},
        ).json()
        st.write("Usuario:", me.get("email"))
