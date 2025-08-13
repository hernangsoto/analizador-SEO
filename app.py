import streamlit as st
import requests
import json
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
import os

# Configuración desde secrets
gcp_config = st.secrets["gcp_oauth_client"]["web"]
CLIENT_ID = gcp_config["client_id"]
CLIENT_SECRET = gcp_config["client_secret"]
REDIRECT_URI = gcp_config["redirect_uris"][0]
AUTH_URI = gcp_config["auth_uri"]
TOKEN_URI = gcp_config["token_uri"]

# Scopes que vamos a pedir (Search Console + Sheets)
SCOPES = [
    "https://www.googleapis.com/auth/webmasters.readonly",
    "https://www.googleapis.com/auth/spreadsheets"
]

# 1. Mostrar botón de login si no hay credenciales
if "credentials" not in st.session_state:
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "auth_uri": AUTH_URI,
                "token_uri": TOKEN_URI,
                "redirect_uris": [REDIRECT_URI]
            }
        },
        scopes=SCOPES
    )

    auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline", include_granted_scopes="true")
    st.markdown(f"[🔑 Iniciar sesión con Google]({auth_url})")

# 2. Si el usuario vuelve con el `code` en la URL
elif "code" in st.query_params:
    code = st.query_params["code"]
    flow.fetch_token(code=code)
    creds = flow.credentials
    st.session_state["credentials"] = creds_to_dict(creds)
    st.success("✅ Autenticación completada")

# 3. Función para usar las credenciales
def creds_to_dict(creds):
    return {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes
    }
