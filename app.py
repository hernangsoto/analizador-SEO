# app.py
import os
import streamlit as st
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

st.set_page_config(page_title="Login con Google", page_icon="🔐")

# ----------------- Config -----------------
def load_config():
    client_id = st.secrets.get("CLIENT_ID") or os.getenv("CLIENT_ID")
    client_secret = st.secrets.get("CLIENT_SECRET") or os.getenv("CLIENT_SECRET")
    redirect_uri = st.secrets.get("REDIRECT_URI") or os.getenv("REDIRECT_URI")

    if not (client_id and client_secret and redirect_uri):
        st.error("Faltan CLIENT_ID / CLIENT_SECRET / REDIRECT_URI en Secrets.")
        st.stop()

    # Tipo 'web' para despliegues en Streamlit Cloud
    client_config = {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],            # Debe coincidir EXACTO con Secrets y GCP
            "javascript_origins": ["https://hernangsoto.streamlit.app"],
        }
    }
    return client_config, redirect_uri

def new_flow(client_config, redirect_uri):
    scopes = ["openid", "email", "profile"]
    return Flow.from_client_config(client_config, scopes=scopes, redirect_uri=redirect_uri)

def build_auth_url(flow):
    url, _ = flow.authorization_url(
        prompt="consent",               # Fuerza pantalla de consentimiento
        access_type="offline",          # Pide refresh_token
        include_granted_scopes="true",
    )
    return url

def clear_qp():
    # Usar SOLO la API nueva de query params
    try:
        st.query_params.clear()
    except Exception:
        pass

# ----------------- App -----------------
def main():
    st.title("🔐 Login con Google")
    st.caption("Ingresá tu nombre y luego iniciá sesión con Google.")

    # 1) Pedir nombre (y guardarlo limpio)
    nombre = st.text_input("Tu nombre")
    if nombre and nombre.strip():
        st.session_state["nombre"] = nombre.strip()

    client_config, redirect_uri = load_config()

    # 2) Generar la URL de login (una sola vez)
    if "auth_url" not in st.session_state:
        tmp_flow = new_flow(client_config, redirect_uri)   # Flow temporal solo para generar URL
        st.session_state.auth_url = build_auth_url(tmp_flow)

    # 3) ¿Volvimos de Google con ?code=... ?
    code = st.query_params.get("code")
    error = st.query_params.get("error")

    if error:
        st.error(f"Error de Google OAuth: {error}")
        clear_qp()

    if code and not st.session_state.get("credentials"):
        with st.spinner("Autenticando..."):
            try:
                # Re-crear un Flow NUEVO para canjear el code (evita depender de session_state)
                flow = new_flow(client_config, redirect_uri)
                flow.fetch_token(code=code)
                st.session_state.credentials = flow.credentials
                st.session_state.just_logged_in = True
                clear_qp()
                st.rerun()
            except Exception as e:
                st.error(f"Error al autenticar: {e}")
                st.stop()

    # 4) Mensaje de éxito si acaba de loguear (una sola vez)
    if st.session_state.get("just_logged_in"):
        try:
            oauth2 = build("oauth2", "v2", credentials=st.session_state.credentials)
            me = oauth2.userinfo().get().execute()
            st.toast("Cuenta logueada exitosamente ✅", icon="✅")
            st.success(
                f"✅ Cuenta logueada exitosamente.\n\n"
                f"Hola {st.session_state.get('nombre','')}, iniciaste sesión como **{me.get('email','tu cuenta de Google')}**."
            )
        except Exception:
            st.toast("Cuenta logueada exitosamente ✅", icon="✅")
            st.success(
                f"✅ Cuenta logueada exitosamente.\n\n"
                f"Hola {st.session_state.get('nombre','')}."
            )
        del st.session_state["just_logged_in"]

    # 5) UI según estado
    if not st.session_state.get("credentials"):
        if not st.session_state.get("nombre"):
            st.info("Ingresá tu nombre para continuar.")
        else:
            st.write(f"¡Hola **{st.session_state['nombre']}**! Iniciá sesión con Google:")
            # Botón de login activo
            if st.button("🔓 Sign in with Google", type="primary", use_container_width=True):
                # Redirige en la MISMA pestaña
                st.markdown(
                    f'<script>window.location.href="{st.session_state.auth_url}";</script>',
                    unsafe_allow_html=True,
                )
                st.stop()

        with st.expander("Debug (opcional)"):
            st.code(
                f"REDIRECT_URI: {redirect_uri}\n"
                f"Auth URL:\n{st.session_state.get('auth_url','')}"
            )
        return

    # 6) Ya autenticado
    # Mostrar botón deshabilitado en lugar del login
    st.button("✅ Ya estás logueado", disabled=True, use_container_width=True)

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
        st.info("No se pudo obtener el perfil, pero el login está OK.")

    # 7) Cerrar sesión
    if st.button("Cerrar sesión"):
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        clear_qp()
        st.rerun()

if __name__ == "__main__":
    main()
