import streamlit as st
import requests
import urllib.parse

client_id = st.secrets["gcp_oauth_client.web"]["client_id"]
client_secret = st.secrets["gcp_oauth_client.web"]["client_secret"]
redirect_uri = st.secrets["gcp_oauth_client.web"]["redirect_uri"]

# Paso 1: Botón para iniciar sesión
if "code" not in st.query_params:
    auth_url = (
        "https://accounts.google.com/o/oauth2/auth"
        "?response_type=code"
        f"&client_id={client_id}"
        f"&redirect_uri={urllib.parse.quote(redirect_uri)}"
        "&scope=openid%20email%20profile"
        "&access_type=offline"
        "&prompt=consent"
    )
    st.markdown(f"[Iniciar sesión con Google]({auth_url})")

# Paso 2: Si Google redirige con ?code=...
else:
    code = st.query_params["code"]
    token_url = "https://oauth2.googleapis.com/token"
    data = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    r = requests.post(token_url, data=data)
    token_info = r.json()

    # Obtener datos del usuario
    user_info = requests.get(
        "https://www.googleapis.com/oauth2/v1/userinfo",
        params={"alt": "json", "access_token": token_info["access_token"]},
    ).json()

    st.write("✅ Sesión iniciada")
    st.json(user_info)
