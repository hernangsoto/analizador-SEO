import os
import urllib.parse as urlparse
from urllib.parse import urlencode

import streamlit as st
from google_auth_oauthlib.flow import Flow
from google.oauth2 import id_token
from google.auth.transport import requests as grequests

st.set_page_config(page_title="Login con Google + Nombre", page_icon="🔐")

# --- UI: Nombre ---
st.title("🔐 Demo: Tu nombre + Login con Google")
if "nombre" not in st.session_state:
    st.session_state["nombre"] = ""

st.session_state["nombre"] = st.text_input(
    "Tu nombre",
    value=st.session_state["nombre"],
    placeholder="Ej: Hernán Soto"
)

# --- Config de OAuth (desde secrets.toml) ---
CLIENT_ID = st.secrets["oauth"]["client_id"]
CLIENT_SECRET = st.secrets["oauth"]["client_secret"]
REDIRECT_URI = st.secrets["oauth"]["redirect_uri"]  # debe coincidir EXACTAMENTE con lo configurado en Google
SCOPES = ["openid", "email", "profile"]

# --- Helpers ---
def build_flow():
    client_config = {
        "web": {
            "client_id": CLIENT_ID,
            "project_id": "streamlit-oauth",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_secret": CLIENT_SECRET,
            "redirect_uris": [REDIRECT_URI],
            "javascript_origins": [REDIRECT_URI.rsplit("/", 1)[0]],
        }
    }
    flow = Flow.from_client_config(client_config, scopes=SCOPES)
    flow.redirect_uri = REDIRECT_URI
    return flow

def _as_doseq_dict(q: dict) -> dict:
    # Asegura listas para urlencode(..., doseq=True)
    return {k: (v if isinstance(v, list) else [v]) for k, v in q.items()}

def get_current_query_string():
    q = dict(st.query_params)  # Mapping → dict
    return urlencode(_as_doseq_dict(q), doseq=True)

def full_current_url():
    qs = get_current_query_string()
    return REDIRECT_URI + (f"?{qs}" if qs else "")

# --- Estado de sesión ---
if "google_user" not in st.session_state:
    st.session_state["google_user"] = None  # dict con info del usuario
if "oauth_state" not in st.session_state:
    st.session_state["oauth_state"] = None

# --- Manejo del callback OAuth ---
query_params = dict(st.query_params)  # ahora es un mapping; lo convertimos a dict simple
has_code_and_state = "code" in query_params and "state" in query_params

if has_code_and_state and st.session_state.get("oauth_state"):
    try:
        flow = build_flow()
        # Verificar que el state coincida
        if query_params["state"] != st.session_state["oauth_state"]:
            st.warning("El parámetro 'state' no coincide. Volvé a intentar el login.")
        else:
            # Intercambiar el código por tokens
            flow.fetch_token(authorization_response=full_current_url())
            creds = flow.credentials

            # Verificar el ID token y obtener info del usuario
            idinfo = id_token.verify_oauth2_token(
                creds._id_token,
                grequests.Request(),
                CLIENT_ID,
            )
            email = idinfo.get("email")
            picture = idinfo.get("picture")
            given_name = idinfo.get("given_name") or ""
            family_name = idinfo.get("family_name") or ""

            # Validar que sea @gmail.com (o permitir dominios Workspace si querés)
            if not email or not email.lower().endswith("@gmail.com"):
                st.error("Necesitás iniciar sesión con una cuenta @gmail.com.")
            else:
                st.session_state["google_user"] = {
                    "email": email,
                    "picture": picture,
                    "given_name": given_name,
                    "family_name": family_name,
                }

            # Limpiar la query para no dejar ?code=... en la URL
            st.query_params.clear()

    except Exception as e:
        st.error(f"Falló el intercambio de token: {e}")
        st.query_params.clear()  # limpiar la query si algo falló

# --- Botón de Login ---
if st.session_state["google_user"] is None:
    st.write("Primero completá tu nombre y después hacé login con Google.")

    disabled = len(st.session_state["nombre"].strip()) == 0

    if st.button("Iniciar sesión con Google", disabled=disabled):
        flow = build_flow()
        auth_url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",  # fuerza pantalla de consentimiento la primera vez
        )
        st.session_state["oauth_state"] = state
        st.query_params.clear()  # limpiamos antes de redirigir
        st.write("Redirigiendo a Google…")
        st.markdown(f"[➡️ Continuar con Google]({auth_url})")

else:
    # Usuario autenticado
    user = st.session_state["google_user"]
    nombre = st.session_state["nombre"].strip() or user.get("given_name") or "¡Hola!"

    col1, col2 = st.columns([1, 5])
    with col1:
        if user.get("picture"):
            st.image(user["picture"], width=80)
    with col2:
        st.success(f"¡Bienvenido/a, {nombre}!")
        st.write(f"**Email:** {user['email']}")

    if st.button("Cerrar sesión"):
        st.session_state["google_user"] = None
        st.session_state["oauth_state"] = None
        st.query_params.clear()
        st.rerun()
