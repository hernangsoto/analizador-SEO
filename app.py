import os
import streamlit as st
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

st.set_page_config(page_title="Login con Google", page_icon="🔐")

# -----------------
# Configuración
# -----------------
def load_config():
    client_id = st.secrets.get("CLIENT_ID") or os.getenv("CLIENT_ID")
    client_secret = st.secrets.get("CLIENT_SECRET") or os.getenv("CLIENT_SECRET")
    redirect_uri = st.secrets.get("REDIRECT_URI") or os.getenv("REDIRECT_URI")
    if not (client_id and client_secret and redirect_uri):
        st.error("Faltan CLIENT_ID / CLIENT_SECRET / REDIRECT_URI en Secrets.")
        st.stop()

    # Usamos formato 'web' para apps deployadas
    client_config = {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
            "javascript_origins": ["https://hernangsoto.streamlit.app"],
        }
    }
    return client_config, redirect_uri

def init_flow(client_config, redirect_uri):
    scopes = ["openid", "email", "profile"]
    return Flow.from_client_config(client_config, scopes=scopes, redirect_uri=redirect_uri)

def get_auth_url(flow):
    url, _ = flow.authorization_url(
        prompt="consent",
        access_type="offline",
        include_granted_scopes="true",
    )
    return url

def clear_qp():
    try:
        st.query_params.clear()
    except Exception:
        pass

# -----------------
# App
# -----------------
def main():
    st.title("🔐 Login con Google")
    st.caption("Ingresá tu nombre y luego iniciá sesión con Google.")

    # Nombre
    nombre = st.text_input("Tu nombre")
    if nombre:
        st.session_state["nombre"] = nombre

    client_config, redirect_uri = load_config()

    # Crear Flow y URL una sola vez
    if "auth_flow" not in st.session_state:
        st.session_state.auth_flow = init_flow(client_config, redirect_uri)
        st.session_state.auth_url = get_auth_url(st.session_state.auth_flow)

    # Si volvemos de Google con ?code=...
    code = st.query_params.get("code")
    if code and not st.session_state.get("credentials"):
        with st.spinner("Autenticando..."):
            try:
                # Usar el MISMO flow para validar 'state'
                st.session_state.auth_flow.fetch_token(code=code)
                st.session_state.credentials = st.session_state.auth_flow.credentials
                # Marcamos que el login se completó recién ahora
                st.session_state.just_logged_in = True
                clear_qp()
                st.rerun()
            except Exception as e:
                st.error(f"Error al autenticar: {e}")
                st.stop()

    # Si acabamos de loguearnos, mostramos éxito
    if st.session_state.get("just_logged_in"):
        try:
            oauth2 = build("oauth2", "v2", credentials=st.session_state.credentials)
            me = oauth2.userinfo().get().execute()
            st.success(f"✅ ¡Hola {st.session_state.get('nombre','')}! "
                       f"Se inició sesión correctamente como **{me.get('email','tu cuenta de Google')}**.")
        except Exception:
            st.success(f"✅ ¡Hola {st.session_state.get('nombre','')}! Sesión iniciada correctamente.")
        # La mostramos solo una vez
        del st.session_state["just_logged_in"]

    # UI según estado
    if not st.session_state.get("credentials"):
        if not nombre:
            st.info("Ingresá tu nombre para continuar.")
        else:
            st.write(f"¡Hola **{nombre}**! Iniciá sesión con Google:")
            if st.button("🔓 Sign in with Google", type="primary", use_container_width=True):
                # Redirigir en la misma pestaña para no perder session_state
                st.markdown(
                    f'<script>window.location.href="{st.session_state.auth_url}";</script>',
                    unsafe_allow_html=True,
                )
                st.stop()

        with st.expander("Detalles técnicos"):
            st.code(f"REDIRECT_URI: {redirect_uri}\nScopes: openid email profile")
        return

    # Autenticado: opcional, mostrar perfil
    try:
        oauth2 = build("oauth2", "v2", credentials=st.session_state.credentials)
        me = oauth2.userinfo().get().execute()
        col1, col2 = st.columns([1, 2])
        with col1:
            if me.get("picture"):
                st.image(me["picture"])
        with col2:
            st.write("**Tu cuenta**")
            st.json({"name": me.get("name"), "email": me.get("email"), "id": me.get("id")})
    except Exception:
        st.info("No se pudo obtener el perfil, pero el login está OK.")

    # Botón Cerrar sesión
    if st.button("Cerrar sesión"):
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        clear_qp()
        st.rerun()

if __name__ == "__main__":
    main()
