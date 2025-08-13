# app.py
import streamlit as st
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
import os

st.set_page_config(page_title="Login con Google", page_icon="🔐")

# -----------------
# Config / Secrets
# -----------------
def load_config():
    client_id = st.secrets.get('CLIENT_ID') or os.getenv('CLIENT_ID')
    client_secret = st.secrets.get('CLIENT_SECRET') or os.getenv('CLIENT_SECRET')
    redirect_uri = st.secrets.get('REDIRECT_URI') or os.getenv('REDIRECT_URI')

    if not (client_id and client_secret and redirect_uri):
        st.error("Faltan CLIENT_ID / CLIENT_SECRET / REDIRECT_URI en Secrets.")
        st.stop()

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://accounts.google.com/o/oauth2/token",
            "redirect_uris": [redirect_uri],
        }
    }
    return client_config, redirect_uri

def init_flow(client_config, redirect_uri):
    scopes = ["openid", "email", "profile"]
    return Flow.from_client_config(client_config, scopes=scopes, redirect_uri=redirect_uri)

def get_auth_url(flow):
    # refresh token + consentimiento explícito
    auth_url, _ = flow.authorization_url(
        prompt="consent",
        access_type="offline",
        include_granted_scopes="true",
    )
    return auth_url

# -----------------
# App
# -----------------
def main():
    st.title("🔐 Login con Google")
    st.caption("Primero ingresá tu nombre y después iniciá sesión con Google.")

    # 1) Nombre
    nombre = st.text_input("Tu nombre")
    if nombre:
        st.session_state["nombre"] = nombre

    client_config, redirect_uri = load_config()

    # 2) Crear una sola vez el Flow y la URL
    if "auth_flow" not in st.session_state:
        st.session_state.auth_flow = init_flow(client_config, redirect_uri)
        st.session_state.auth_url = get_auth_url(st.session_state.auth_flow)

    # 3) Si volvemos de Google con ?code=... canjeamos el token
    qp = getattr(st, "query_params", None) or st.experimental_get_query_params()
    code = None
    if qp and "code" in qp and not st.session_state.get("credentials"):
        code = qp["code"][0] if isinstance(qp["code"], list) else qp["code"]

    if code and not st.session_state.get("credentials"):
        with st.spinner("Autenticando..."):
            try:
                # Usamos el MISMO flow guardado en sesión para validar 'state'
                st.session_state.auth_flow.fetch_token(code=code)
                st.session_state.credentials = st.session_state.auth_flow.credentials
                # Limpiar query params
                try:
                    st.query_params.clear()
                except Exception:
                    st.experimental_set_query_params()
                st.rerun()
            except Exception as e:
                st.error(f"Error al autenticar: {e}")

    # 4) UI según estado
    if not st.session_state.get("credentials"):
        if not nombre:
            st.info("Ingresá tu nombre para continuar.")
        else:
            st.write(f"¡Hola **{nombre}**! Iniciá sesión con Google:")
            if st.button("🔓 Sign in with Google", type="primary", use_container_width=True):
                # Redirige en la MISMA pestaña (evita perder el state del Flow)
                st.markdown(
                    f'<script>window.location.href="{st.session_state.auth_url}";</script>',
                    unsafe_allow_html=True,
                )
                st.stop()

        with st.expander("Detalles técnicos"):
            st.code(f"REDIRECT_URI: {redirect_uri}\nScopes: openid email profile")
        return

    # 5) Autenticado: mostrar datos básicos
    creds = st.session_state.credentials
    st.success(f"¡Hola {st.session_state.get('nombre','')}! Sesión iniciada correctamente.")

    # Traer perfil con la API OAuth2
    try:
        oauth2 = build("oauth2", "v2", credentials=creds)
        me = oauth2.userinfo().get().execute()
        col1, col2 = st.columns([1, 2])
        with col1:
            if me.get("picture"):
                st.image(me["picture"])
        with col2:
            st.write("**Tu cuenta**")
            st.json({"name": me.get("name"), "email": me.get("email"), "id": me.get("id")})
    except Exception:
        st.info("No se pudo obtener el perfil. Igual estás logueado ✔️")

    # Cerrar sesión
    if st.button("Cerrar sesión"):
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        try:
            st.query_params.clear()
        except Exception:
            st.experimental_set_query_params()
        st.rerun()

if __name__ == "__main__":
    main()
