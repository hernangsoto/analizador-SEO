import streamlit as st
from streamlit_oauth import OAuth2Component

# Datos de tu app en Google Cloud
CLIENT_ID = "828947627584-2gb6qjlqtci802716dhbk1aa499nbom9.apps.googleusercontent.com"
CLIENT_SECRET = "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"
AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REDIRECT_URI = "https://hernangsoto.streamlit.app"  # tu URL de Streamlit Cloud

# Scopes para Gmail
SCOPES = ["openid", "email", "profile"]

st.set_page_config(page_title="Login con Google", page_icon="🔑")

# Instanciamos el componente OAuth
oauth2 = OAuth2Component(
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
    authorize_url=AUTHORIZE_URL,
    token_url=TOKEN_URL,
    redirect_uri=REDIRECT_URI,
)

st.title("🔑 Login con Gmail")
name = st.text_input("Ingrese su nombre:")

if name:
    result = oauth2.authorize_button(
        name="Login con Google",
        icon="🔐",
        scopes=SCOPES,
        key="google"
    )

    if result:
        st.success(f"✅ Hola {name}, inicio de sesión exitoso con la cuenta: {result['email']}")
        st.json(result)
else:
    st.info("Por favor, ingrese su nombre antes de continuar.")
