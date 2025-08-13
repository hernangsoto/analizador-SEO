import streamlit as st
from google_auth_oauthlib.flow import Flow
from google.oauth2 import id_token
from google.auth.transport import requests as grequests
import json, os, urllib.parse, time

# ----------------------------
# Configuración
# ----------------------------
# Define la URL base donde corre tu app. Debe coincidir EXACTAMENTE con el
# "Authorized redirect URI" en Google Cloud. Para local: "http://localhost:8501"
BASE_URL = st.secrets.get("app_base_url", "http://localhost:8501")

# Credenciales del cliente OAuth (del JSON de Google Cloud)
# Recomendado: ponerlas en .streamlit/secrets.toml bajo la clave "google_oauth_client_json"
# como un JSON completo en una sola línea (string). Ej:
# google_oauth_client_json = '{"web": {"client_id": "...","client_secret":"...","auth_uri":"https://accounts.google.com/o/oauth2/auth","token_uri":"https://oauth2.googleapis.com/token"}}'
CLIENT_JSON = st.secrets.get("google_oauth_client_json")

if not CLIENT_JSON:
    st.error(
        "Falta configurar 'google_oauth_client_json' en secrets.\n\n"
        "Ve a Google Cloud Console → Credentials → Create OAuth client ID (Web app).\n"
        "Copia el JSON del cliente y guárdalo en .streamlit/secrets.toml."
    )
    st.stop()

try:
    client_config = json.loads(CLIENT_JSON)
except Exception as e:
    st.error(f"El JSON de credenciales en secrets no es válido: {e}")
    st.stop()

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]

# ----------------------------
# Helpers
# ----------------------------

def build_flow(redirect_uri: str) -> Flow:
    flow = Flow.from_client_config(client_config, scopes=SCOPES)
    flow.redirect_uri = redirect_uri
    return flow


def current_full_url() -> str:
    # Reconstruye la URL actual usando BASE_URL y los query params de Streamlit
    # (Google nos devuelve ?state=...&code=... sobre la misma BASE_URL)
    qp = st.query_params  # Streamlit >= 1.30
    if qp:
        return f"{BASE_URL}?{urllib.parse.urlencode(qp, doseq=True)}"
    return BASE_URL


def verify_and_extract_userinfo(creds) -> dict:
    """Verifica el ID token y extrae datos básicos del usuario (email, nombre, foto)."""
    try:
        # Verifica el ID token contra Google
        idinfo = id_token.verify_oauth2_token(
            creds.id_token,
            grequests.Request(),
            client_config["web"]["client_id"],
        )
        # idinfo típicamente contiene: email, email_verified, name, picture, sub, etc.
        return {
            "email": idinfo.get("email"),
            "name": idinfo.get("name"),
            "picture": idinfo.get("picture"),
            "sub": idinfo.get("sub"),  # ID único del usuario en Google
        }
    except Exception as e:
        st.warning(f"No se pudo verificar el ID token: {e}")
        return {}


# ----------------------------
# UI
# ----------------------------
st.set_page_config(page_title="Login con Google (Streamlit)", page_icon="🔐")

st.title("🔐 Login con Google en Streamlit")

if "user" not in st.session_state:
    st.session_state.user = None
if "creds" not in st.session_state:
    st.session_state.creds = None
if "oauth_state" not in st.session_state:
    st.session_state.oauth_state = None

# Captura de callback (?code=...&state=...)
qp = st.query_params

# Si ya estamos logueados
if st.session_state.user:
    col1, col2 = st.columns([1, 6])
    with col1:
        if st.session_state.user.get("picture"):
            st.image(st.session_state.user["picture"], caption="Tu foto", use_container_width=True)
    with col2:
        st.success("Sesión iniciada correctamente")
        st.write(f"**Nombre:** {st.session_state.user.get('name')}\n\n**Email:** {st.session_state.user.get('email')}")
        if st.button("Cerrar sesión", type="secondary"):
            # Limpia todo y quita los params de la URL
            st.session_state.user = None
            st.session_state.creds = None
            st.session_state.oauth_state = None
            st.query_params.clear()
            st.rerun()
    st.divider()
    st.write("🔭 Aquí seguiría el contenido protegido de tu app…")
    st.stop()

# Si volvemos del consentimiento (hay 'code' y 'state'), intercambiamos por tokens
if "code" in qp and "state" in qp and st.session_state.oauth_state == qp.get("state"):
    flow = build_flow(BASE_URL)
    try:
        flow.fetch_token(authorization_response=current_full_url())
        creds = flow.credentials
        st.session_state.creds = {
            "token": creds.token,
            "refresh_token": getattr(creds, "refresh_token", None),
            "expiry": int(creds.expiry.timestamp()) if getattr(creds, "expiry", None) else None,
            "scopes": creds.scopes,
            "id_token": getattr(creds, "id_token", None),
        }
        userinfo = verify_and_extract_userinfo(creds)
        st.session_state.user = userinfo or {"email": "(desconocido)"}
        # Limpia los params para no reintentar al hacer rerun
        st.query_params.clear()
        st.rerun()
    except Exception as e:
        st.error(f"Error al obtener el token de Google: {e}")
        st.stop()

st.info("Primero, inicia sesión con tu cuenta de Google para continuar.")

# Botón de login → redirige al consentimiento de Google
flow = build_flow(BASE_URL)
auth_url, state = flow.authorization_url(
    access_type="offline",            # refresh_token
    include_granted_scopes="true",
    prompt="consent",                 # fuerza pantalla de consentimiento; cámbialo si no lo necesitas
)
# Guarda el estado para evitar CSRF
st.session_state.oauth_state = state

st.link_button("Continuar con Google", auth_url, type="primary")

st.caption(
    "Al hacer clic serás redirigido a cuentas de Google. Tras aceptar, volverás a esta app.\n"
    "Asegúrate de que el Authorized redirect URI en Google Cloud coincida con '" + BASE_URL + "'."
)

st.divider()

with st.expander("Ver detalles técnicos"):
    st.code(
        """
Scopes usados:
- openid
- https://www.googleapis.com/auth/userinfo.email
- https://www.googleapis.com/auth/userinfo.profile

Puntos clave:
- BASE_URL debe coincidir con el Authorized redirect URI.
- Guardamos state en session_state para proteger contra CSRF.
- Usamos el ID token para extraer nombre/email/foto. Si prefieres, puedes llamar a
  https://www.googleapis.com/oauth2/v3/userinfo con el access token para obtener el perfil.
        """,
        language="text",
    )
