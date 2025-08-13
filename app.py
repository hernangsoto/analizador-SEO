# app.py
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

    # ⚠️ Usamos formato 'web' (mejor para apps deployadas)
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
    # Flow va a tomar redirect_uri desde aquí; debe estar en la lista autorizada
    return Flow.from_client_config(client_config, scopes=scopes, redirect_uri=redirect_uri)

def get_auth_url(flow):
    # Forzamos prompt=consent para obtener refresh_token la primera vez
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

    # 1) Pedir nombre
    nombre = st.text_input("Tu nombre")
    if nombre:
        st.session_state["nombre"] = nombre

    client_config, redirect_uri = load_config()

    # 2) Crear Flow una sola vez y armar URL
    if "auth_flow" not in st.session_state:
        st.session_state.auth_flow = init_flow(client_config, redirect_uri)
        st.session_state.auth_url = get_auth_url(st.session_state.auth_flow)

    # 3) Si volvemos de Google con ?code=..., canjear token
    code = st.query_params.get("code")
    if code and not st.session_state.get("credentials"):
        with st.spinner("Autenticando..."):
            try:
                # IMPORTANTE: usamos el MISMO flow guardado en session_state (valida 'state')
                st.session_state.auth_flow.fetch_token(code=code)
                st.session_state.credentials = st.session_state.auth_flow.credentials
                clear_qp()
                st.rerun()
            except Exception as e:
                st.error(f"Error al autenticar: {e}")
                st.stop()

    # 4) UI según estado
    if not st.session_state.get("credentials"):
        if not nombre:
            st.info("Ingresá tu nombre para continuar.")
        else:
            st.write(f"¡Hola **{nombre}**! Iniciá sesión con Google:")
            if st.button("🔓 Sign in with Google", type="primary", use_container_width=True):
                # Redirigir en la misma pestaña (evita perder session_state)
                st.markdown(
                    f'<script>window.location.href="{st.session_state.auth_url}";</script>',
                    unsafe_allow_html=True,
                )
                st.stop()

        with st.expander("Debug (opcional)"):
            st.code(
                f"REDIRECT_URI: {redirect_uri}\n"
                f"Auth URL generado:\n{st.session_state.get('auth_url','')}"
            )
        return

    # 5) Autenticado
    st.success(f"¡Hola {st.session_state.get('nombre','')}! Sesión iniciada correctamente.")
    # Perfil básico (opcional)
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
        st.info("No se pudo obtener el perfil (scope openid/email/profile suficiente para login).")

    if st.button("Cerrar sesión"):
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        clear_qp()
        st.rerun()

if __name__ == "__main__":
    main()
