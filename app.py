import streamlit as st
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
import requests, urllib.parse

"""
MVP — Prueba básica de Login con Google (Streamlit) ✅
-----------------------------------------------------
Qué hace:
1) Carga el cliente OAuth desde secrets anidados ([gcp_oauth_client]→[web]) o deja pegar client_id/client_secret/redirect_uri.
2) Botón "Iniciar sesión" → redirige a Google → vuelve con ?code=...
3) Intercambia el code por tokens, muestra tu email y lista propiedades de Search Console.

Si esto funciona, luego integramos tu analizador y Google Sheets.

Secrets (Streamlit Cloud → Settings → Secrets):
[gcp_oauth_client]
  [gcp_oauth_client.web]
  client_id = "8289...apps.googleusercontent.com"
  client_secret = "GOCSPX-..."
  auth_uri = "https://accounts.google.com/o/oauth2/auth"
  token_uri = "https://oauth2.googleapis.com/token"
  redirect_uris = ["https://hernangsoto.streamlit.app/"]
"""

SCOPES = [
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/webmasters.readonly",
]

st.set_page_config(page_title="Login Google — MVP", page_icon="🔑", layout="centered")
st.title("🔑 Prueba básica: Login con Google")

# -------------------------------------------------------------
# 1) Config OAuth (secrets primero, UI fallback)
# -------------------------------------------------------------

def load_oauth_cfg():
    cfg = None
    try:
        cfg = dict(st.secrets["gcp_oauth_client"]["web"])  # TOML anidado correcto
    except Exception:
        pass
    if cfg:
        return cfg

    st.warning("No encontré gcp_oauth_client → web en Secrets. Pegá los datos para probar.")
    c1, c2 = st.columns(2)
    with c1:
        cid = st.text_input("client_id")
        csecret = st.text_input("client_secret", type="password")
    with c2:
        redirect_uri = st.text_input("redirect_uri", value="https://hernangsoto.streamlit.app/")
    if cid and csecret and redirect_uri:
        return {
            "client_id": cid,
            "client_secret": csecret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    st.stop()


def build_flow(client_config: dict, redirect_uri: str):
    ccfg = client_config if "web" in client_config else {"web": client_config}
    flow = Flow.from_client_config(ccfg, scopes=SCOPES)
    flow.redirect_uri = redirect_uri
    return flow


def start_login(oauth_cfg: dict):
    flow = build_flow(oauth_cfg, oauth_cfg["redirect_uris"][0])
    auth_url, state = flow.authorization_url(
        access_type="offline", include_granted_scopes="true", prompt="consent",
    )
    st.session_state["_oauth_cfg"] = oauth_cfg
    st.session_state["_redirect_uri"] = oauth_cfg["redirect_uris"][0]
    st.session_state["_oauth_state"] = state
    st.session_state["_auth_url"] = auth_url


def handle_callback():
    qp = {}
    try:
        qp = dict(st.query_params)
    except Exception:
        pass
    if qp.get("code") and st.session_state.get("_oauth_cfg"):
        code = qp["code"][0] if isinstance(qp.get("code"), list) else qp.get("code")
        state_back = qp.get("state")
        if isinstance(state_back, list):
            state_back = state_back[0]
        if st.session_state.get("_oauth_state") and state_back and state_back != st.session_state.get("_oauth_state"):
            st.error("Estado OAuth inválido. Reintentá el login.")
            st.stop()
        flow = build_flow(st.session_state["_oauth_cfg"], st.session_state["_redirect_uri"])
        flow.fetch_token(code=code)
        st.session_state["oauth_creds"] = flow.credentials
        st.success("✅ Login correcto")


def get_userinfo(access_token: str):
    resp = requests.get(
        "https://www.googleapis.com/oauth2/v1/userinfo",
        params={"alt": "json", "access_token": access_token},
        timeout=15,
    )
    return resp.json()


def list_gsc_properties(creds):
    try:
        gsc = build("webmasters", "v3", credentials=creds)
        resp = gsc.sites().list().execute() or {}
        return [e.get("siteUrl") for e in resp.get("siteEntry", [])]
    except Exception as e:
        st.error(f"No pude listar propiedades: {e}")
        return []

# -------------------------------------------------------------
# 2) UI
# -------------------------------------------------------------

oauth_cfg = load_oauth_cfg()

if st.session_state.get("oauth_creds"):
    creds = st.session_state["oauth_creds"]

    # Logout
    with st.sidebar:
        if st.button("Cerrar sesión"):
            st.session_state["oauth_creds"] = None
            st.rerun()

    # Mostrar email del usuario
    info = get_userinfo(creds.token)
    st.success(f"Sesión iniciada como: {info.get('email')}")

    # Listar propiedades GSC
    st.subheader("Propiedades de Search Console")
    sites = list_gsc_properties(creds)
    if sites:
        st.write("
".join(sites))
    else:
        st.info("No hay propiedades o faltan permisos.")
else:
    st.info("Iniciá sesión con tu cuenta de Google.")
    colA, colB = st.columns([1, 1])
    with colA:
        if st.button("🔐 Iniciar sesión con Google"):
            start_login(oauth_cfg)
    with colB:
        if st.session_state.get("_auth_url"):
            st.link_button("Abrir Google Login", st.session_state["_auth_url"])  

    # Manejar callback si ya volvió con ?code=
    handle_callback()
import streamlit as st
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
import requests, urllib.parse

"""
MVP — Prueba básica de Login con Google (Streamlit) ✅
-----------------------------------------------------
Qué hace:
1) Carga el cliente OAuth desde secrets anidados ([gcp_oauth_client]→[web]) o deja pegar client_id/client_secret/redirect_uri.
2) Botón "Iniciar sesión" → redirige a Google → vuelve con ?code=...
3) Intercambia el code por tokens, muestra tu email y lista propiedades de Search Console.

Si esto funciona, luego integramos tu analizador y Google Sheets.

Secrets (Streamlit Cloud → Settings → Secrets):
[gcp_oauth_client]
  [gcp_oauth_client.web]
  client_id = "8289...apps.googleusercontent.com"
  client_secret = "GOCSPX-..."
  auth_uri = "https://accounts.google.com/o/oauth2/auth"
  token_uri = "https://oauth2.googleapis.com/token"
  redirect_uris = ["https://hernangsoto.streamlit.app/"]
"""

SCOPES = [
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/webmasters.readonly",
]

st.set_page_config(page_title="Login Google — MVP", page_icon="🔑", layout="centered")
st.title("🔑 Prueba básica: Login con Google")

# -------------------------------------------------------------
# 1) Config OAuth (secrets primero, UI fallback)
# -------------------------------------------------------------

def load_oauth_cfg():
    cfg = None
    try:
        cfg = dict(st.secrets["gcp_oauth_client"]["web"])  # TOML anidado correcto
    except Exception:
        pass
    if cfg:
        return cfg

    st.warning("No encontré gcp_oauth_client → web en Secrets. Pegá los datos para probar.")
    c1, c2 = st.columns(2)
    with c1:
        cid = st.text_input("client_id")
        csecret = st.text_input("client_secret", type="password")
    with c2:
        redirect_uri = st.text_input("redirect_uri", value="https://hernangsoto.streamlit.app/")
    if cid and csecret and redirect_uri:
        return {
            "client_id": cid,
            "client_secret": csecret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    st.stop()


def build_flow(client_config: dict, redirect_uri: str):
    ccfg = client_config if "web" in client_config else {"web": client_config}
    flow = Flow.from_client_config(ccfg, scopes=SCOPES)
    flow.redirect_uri = redirect_uri
    return flow


def start_login(oauth_cfg: dict):
    flow = build_flow(oauth_cfg, oauth_cfg["redirect_uris"][0])
    auth_url, state = flow.authorization_url(
        access_type="offline", include_granted_scopes="true", prompt="consent",
    )
    st.session_state["_oauth_cfg"] = oauth_cfg
    st.session_state["_redirect_uri"] = oauth_cfg["redirect_uris"][0]
    st.session_state["_oauth_state"] = state
    st.session_state["_auth_url"] = auth_url


def handle_callback():
    qp = {}
    try:
        qp = dict(st.query_params)
    except Exception:
        pass
    if qp.get("code") and st.session_state.get("_oauth_cfg"):
        code = qp["code"][0] if isinstance(qp.get("code"), list) else qp.get("code")
        state_back = qp.get("state")
        if isinstance(state_back, list):
            state_back = state_back[0]
        if st.session_state.get("_oauth_state") and state_back and state_back != st.session_state.get("_oauth_state"):
            st.error("Estado OAuth inválido. Reintentá el login.")
            st.stop()
        flow = build_flow(st.session_state["_oauth_cfg"], st.session_state["_redirect_uri"])
        flow.fetch_token(code=code)
        st.session_state["oauth_creds"] = flow.credentials
        st.success("✅ Login correcto")


def get_userinfo(access_token: str):
    resp = requests.get(
        "https://www.googleapis.com/oauth2/v1/userinfo",
        params={"alt": "json", "access_token": access_token},
        timeout=15,
    )
    return resp.json()


def list_gsc_properties(creds):
    try:
        gsc = build("webmasters", "v3", credentials=creds)
        resp = gsc.sites().list().execute() or {}
        return [e.get("siteUrl") for e in resp.get("siteEntry", [])]
    except Exception as e:
        st.error(f"No pude listar propiedades: {e}")
        return []

# -------------------------------------------------------------
# 2) UI
# -------------------------------------------------------------

oauth_cfg = load_oauth_cfg()

if st.session_state.get("oauth_creds"):
    creds = st.session_state["oauth_creds"]

    # Logout
    with st.sidebar:
        if st.button("Cerrar sesión"):
            st.session_state["oauth_creds"] = None
            st.rerun()

    # Mostrar email del usuario
    info = get_userinfo(creds.token)
    st.success(f"Sesión iniciada como: {info.get('email')}")

    # Listar propiedades GSC
    st.subheader("Propiedades de Search Console")
    sites = list_gsc_properties(creds)
    if sites:
        st.write("
".join(sites))
    else:
        st.info("No hay propiedades o faltan permisos.")
else:
    st.info("Iniciá sesión con tu cuenta de Google.")
    colA, colB = st.columns([1, 1])
    with colA:
        if st.button("🔐 Iniciar sesión con Google"):
            start_login(oauth_cfg)
    with colB:
        if st.session_state.get("_auth_url"):
            st.link_button("Abrir Google Login", st.session_state["_auth_url"])  

    # Manejar callback si ya volvió con ?code=
    handle_callback()
