import streamlit as st
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/webmasters.readonly",
]

st.set_page_config(page_title="OAuth GSC — MVP", page_icon="🟢", layout="centered")
st.title("🟢 OAuth GSC — MVP (desde cero)")

def load_oauth_cfg():
    cfg = None
    try:
        cfg = dict(st.secrets["gcp_oauth_client"]["web"])
    except Exception:
        pass
    if cfg:
        return cfg

    st.warning("No encontré gcp_oauth_client → web en Secrets. Pegá los datos para probar.")
    cid = st.text_input("client_id")
    csecret = st.text_input("client_secret", type="password")
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
    redirect_uri = oauth_cfg["redirect_uris"][0]
    flow = build_flow(oauth_cfg, redirect_uri)
    auth_url, state = flow.authorization_url(
        access_type="offline", include_granted_scopes="true", prompt="consent",
    )
    st.session_state["_oauth_cfg"] = oauth_cfg
    st.session_state["_redirect_uri"] = redirect_uri
    st.session_state["_oauth_state"] = state
    st.session_state["_auth_url"] = auth_url

def handle_callback():
    qp = dict(st.query_params)
    if qp.get("code") and st.session_state.get("_oauth_cfg"):
        code = qp["code"][0] if isinstance(qp.get("code"), list) else qp.get("code")
        flow = build_flow(st.session_state["_oauth_cfg"], st.session_state["_redirect_uri"])
        flow.fetch_token(code=code)
        st.session_state["oauth_creds"] = flow.credentials
        st.success("✅ Login correcto")

oauth_cfg = load_oauth_cfg()

if st.session_state.get("oauth_creds"):
    creds = st.session_state["oauth_creds"]
    gsc = build("webmasters", "v3", credentials=creds)
    resp = gsc.sites().list().execute() or {}
    sites = [e["siteUrl"] for e in resp.get("siteEntry", [])]
    if sites:
        st.write("\n".join(sites))
    else:
        st.info("No se encontraron propiedades o faltan permisos.")
else:
    if st.button("🔐 Iniciar sesión con Google"):
        start_login(oauth_cfg)
    if st.session_state.get("_auth_url"):
        st.link_button("Abrir Google Login", st.session_state["_auth_url"])
    handle_callback()
