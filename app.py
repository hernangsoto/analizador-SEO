import streamlit as st
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
import os

st.set_page_config(page_title="Prueba Login Google", page_icon="🔑")

# --- Cargar credenciales desde secrets.toml ---
client_id = st.secrets["gcp_oauth_client.web"]["client_id"]
client_secret = st.secrets["gcp_oauth_client.web"]["client_secret"]
redirect_uri = st.secrets["gcp_oauth_client.web"]["redirect_uris"][0]

# --- Botón para iniciar login ---
if "credentials" not in st.session_state:
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [redirect_uri],
            }
        },
        scopes=["https://www.googleapis.com/auth/webmasters.readonly"],
        redirect_uri=redirect_uri
    )

    auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")

    st.markdown(f"[Iniciar sesión con Google]({auth_url})")

else:
    st.success("✅ Ya estás logueado")
    st.write(st.session_state.credentials)
