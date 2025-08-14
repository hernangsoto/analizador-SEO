import os
from urllib.parse import urlencode, urlsplit, urlunsplit

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

# --- Config OAuth (secrets.toml) ---
CLIENT_ID = st.secrets["oauth"]["client_id"]
CLIENT_SECRET = st.secrets["oauth"]["client_secret"]

def normalize_redirect_uri(uri: str) -> str:
    """Deja scheme+host y quita barra final."""
    if not uri:
        return uri
    p = urlsplit(uri.strip())
    norm = urlunsplit((p.scheme, p.netloc, "", "", ""))
    return norm.rstrip("/")

RAW_REDIRECT_URI = st.secrets["oauth"]["redirect_uri"]
REDIRECT_URI = normalize_redirect_uri(RAW_REDIRECT_URI)

SCOPES = ["openid", "email", "profile"]

# Opcional: restringir por dominios (si no está, se acepta cualquier cuenta de Google)
ALLOWED_DOMAINS = st.secrets.get("oauth", {}).get("allowed_domains", None)  # ej: ["gmail.com","tudominio.com"]

# --- Helpers ---
def origin_from_url(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}"

def build_flow():
    client_config = {
        "web": {
            "client_id": CLIENT_ID,
            "project_id": "streamlit-oauth",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_secret": CLIENT_SECRET,
            "redirect_uris": [REDIRECT_URI],  # EXACTO y normalizado
            "javascript_origins": [origin_from_url(REDIRECT_URI)],
        }
    }
    flow = Flow.from_client_config(client_config, scopes=SCOPES)
    flow.redirect_uri = REDIRECT_URI
    return flow

def _as_doseq_dict(q: dict) -> dict:
    return {k: (v if isinstance(v, list) else [v]) for k, v in q.items()}

def get_current_query_string() -> str:
    q = dict(st.query_params)
    return urlencode(_as_doseq_dict(q), doseq=True)

def full_current_url() -> str:
    qs = get_current_query_string()
    return REDIRECT_URI + (f"?{qs}" if qs else "")

def _get_param(q: dict, key: str):
    v = q.get(key)
    if isinstance(v, list):
        return v[0] if v else None
    return v

def email_allowed(address: str) -> bool:
    if not address:
        return False
    if not ALLOWED_DOMAINS:
        return True
    domain = address.split("@")[-1].lower()
    return domain in {d.lower() for d in ALLOWED_DOMAINS}

# --- Estado de sesión ---
st.session_state.setdefault("google_user", None)
st.session_state.setdefault("oauth_state", None)

# --- Diagnóstico de errores devueltos por Google (p.ej., 403 / access_denied) ---
qp = dict(st.query_params)
if "error" in qp:
    err = _get_param(qp, "error")
    desc = _get_param(qp, "error_description") or ""
    st.error(f"Error de OAuth: **{err}**\n\n{desc}")
    # No limpiamos aún para que se vea el mensaje; el usuario puede reintentar.

# --- Callback OAuth ---
code = _get_param(qp, "code")
state = _get_param(qp, "state")
has_code_and_state = bool(code and state)

if has_code_and_state and st.session_state.get("oauth_state"):
    try:
        flow = build_flow()
        if state != st.session_state["oauth_state"]:
            st.warning("El parámetro 'state' no coincide. Volvé a intentar el login.")
        else:
            flow.fetch_token(authorization_response=full_current_url())
            creds = flow.credentials

            # id_token puede estar en .id_token o ._id_token según la versión
            raw_id_token = getattr(creds, "id_token", None) or getattr(creds, "_id_token", None)

            idinfo = id_token.verify_oauth2_token(
                raw_id_token,
                grequests.Request(),
                CLIENT_ID,
            )
            email = idinfo.get("email")
            picture = idinfo.get("picture")
            given_name = idinfo.get("given_name") or ""
            family_name = idinfo.get("family_name") or ""

            if not email_allowed(email):
                allowed_list = ", ".join(ALLOWED_DOMAINS) if ALLOWED_DOMAINS else "cualquier cuenta de Google"
                st.error(f"No autorizado. Dominios permitidos: {allowed_list}.")
            else:
                st.session_state["google_user"] = {
                    "email": email,
                    "picture": picture,
                    "given_name": given_name,
                    "family_name": family_name,
                }
                st.success(f"✅ Login exitoso con tu cuenta **{email}**. ¡Bienvenido/a {given_name or '👤'}!")
                st.balloons()

            # Limpiamos la query (?code=..., ?state=...)
            st.query_params.clear()

    except Exception as e:
        st.error(f"Falló el intercambio de token: {e}")
        st.query_params.clear()

# --- Botón de Login ---
if st.session_state["google_user"] is None:
    st.write("Primero completá tu nombre y después hacé login con Google.")
    disabled = len(st.session_state["nombre"].strip()) == 0

    if st.button("Iniciar sesión con Google", disabled=disabled):
        flow = build_flow()
        auth_url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
        )
        st.session_state["oauth_state"] = state
        st.query_params.clear()

        # Redirigir en la MISMA pestaña (evita perder session_state)
        st.write("Redirigiendo a Google…")
        st.markdown(
            f"""
            <script>
              window.location.href = "{auth_url}";
            </script>
            <a href="{auth_url}" target="_self">➡️ Continuar con Google</a>
            """,
            unsafe_allow_html=True,
        )

else:
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
