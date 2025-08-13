import streamlit as st
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

"""
APP: OAuth-only demo for Google Search Console (Streamlit)
- Solo OAuth (usuario), sin Service Account.
- Lee secrets anidados: [gcp_oauth_client] -> [gcp_oauth_client.web]
- Si faltan secrets, permite pegar client_id / client_secret / redirect_uri en la UI.
- Tras login, lista las propiedades de Search Console para verificar acceso.

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
    "https://www.googleapis.com/auth/webmasters.readonly",
]

st.set_page_config(page_title="OAuth GSC (solo OAuth)", page_icon="🔐", layout="centered")
st.title("🔐 Login con Google (solo OAuth)")


# -------------------------------------------------------------
# Config loader (secrets primero, UI fallback)
# -------------------------------------------------------------
def load_oauth_cfg():
    """Carga config OAuth desde secrets anidados; si faltan, ofrece inputs en UI."""
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


# -------------------------------------------------------------
# OAuth helpers
# -------------------------------------------------------------
def build_flow(client_config: dict, redirect_uri: str):
    """Crea un Flow aceptando estructura 'web' o llana."""
    ccfg = client_config if "web" in client_config else {"web": client_config}
    flow = Flow.from_client_config(ccfg, scopes=SCOPES)
    flow.redirect_uri = redirect_uri
    return flow


def start_login(oauth_cfg: dict):
    """Genera la URL de login y guarda estado en session_state."""
    redirect_uri = oauth_cfg["redirect_uris"][0]
    flow = build_flow(oauth_cfg, redirect_uri)
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    st.session_state["_oauth_cfg"] = oauth_cfg
    st.session_state["_redirect_uri"] = redirect_uri
    st.session_state["_oauth_state"] = state
    st.session_state["_auth_url"] = auth_url


def handle_callback():
    """Procesa ?code=..., valida (opcional) state y guarda credenciales en sesión."""
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

        # Validación opcional de state para evitar CSRF
        if st.session_state.get("_oauth_state") and state_back and state_back != st.session_state.get("_oauth_state"):
            st.error("Estado OAuth inválido. Reintentá el login.")
            st.stop()

        flow = build_flow(st.session_state["_oauth_cfg"], st.session_state["_redirect_uri"])
        flow.fetch_token(code=code)
        st.session_state["oauth_creds"] = flow.credentials
        st.success("✅ Login correcto")


# -------------------------------------------------------------
# UI
# -------------------------------------------------------------
oauth_cfg = load_oauth_cfg()

if st.session_state.get("oauth_creds"):
    creds = st.session_state["oauth_creds"]

    # Botón de logout
    with st.sidebar:
        if st.button("Cerrar sesión"):
            st.session_state["oauth_creds"] = None
            st.rerun()

    # Mostrar propiedades de GSC para validar acceso
    st.subheader("Propiedades de Search Console")
    try:
        gsc = build("webmasters", "v3", credentials=creds)
        resp = gsc.sites().list().execute() or {}
        sites = [e["siteUrl"] for e in resp.get("siteEntry", [])]
        if sites:
            st.success("Acceso OK. Estas son tus propiedades:")
            st.write("\n".join(sites))
        else:
            st.info("No se encontraron propiedades o faltan permisos.")
    except Exception as e:
        st.error(f"No pude listar propiedades: {e}")

else:
    st.info("Iniciá sesión con tu cuenta de Google (debe tener acceso en Search Console).")
    colA, colB = st.columns([1, 1])
    with colA:
        if st.button("🔐 Iniciar sesión con Google"):
            start_login(oauth_cfg)
    with colB:
        if st.session_state.get("_auth_url"):
            st.link_button("Abrir Google Login", st.session_state["_auth_url"])

    # Manejar callback si ya volvió con ?code=
    handle_callback()
