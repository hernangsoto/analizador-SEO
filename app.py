import streamlit as st
import requests

st.set_page_config(layout="wide", page_title="Análisis SEO", page_icon="📊")
st.title("Ejemplo de inicio de sesión con Google en Streamlit")

# ---------------------------
# Helpers
# ---------------------------

def get_user():
    # Compatibilidad con versiones: usa st.user si existe; si no, experimental_user.
    return getattr(st, "user", getattr(st, "experimental_user", None))

def get_first_name(full_name: str | None) -> str:
    if not full_name:
        return "👋"
    return full_name.split()[0]

def sidebar_user_info(user):
    with st.sidebar:
        with st.container():
            c1, c2 = st.columns([1, 3])
            with c1:
                if getattr(user, "picture", None):
                    try:
                        r = requests.get(user.picture, timeout=5)
                        if r.status_code == 200:
                            st.image(r.content, width=96)
                        else:
                            st.warning("No se pudo cargar la imagen.")
                    except Exception as e:
                        st.warning(f"Error al cargar la imagen: {e}")
                else:
                    st.info("Sin imagen de perfil.")
            with c2:
                st.header("Información del usuario", anchor=False)
                st.write(f"**Nombre:** {getattr(user, 'name', '—')}")
                st.write(f"**Correo:** {getattr(user, 'email', '—')}")
        st.divider()
        st.button(":material/logout: Cerrar sesión", on_click=st.logout, use_container_width=True)

# ---------------------------
# Vistas
# ---------------------------

def login_screen():
    st.header("Esta aplicación es privada.")
    st.subheader("Por favor, inicia sesión.")
    st.button(":material/login: Iniciar sesión con Google", on_click=st.login)

def home_screen(user):
    # Mensaje de bienvenida personalizado
    first_name = get_first_name(getattr(user, "name", None))
    st.markdown(f"### Hola, **{first_name}** 👋\nSeleccioná qué análisis querés ejecutar:")

    # Opciones de análisis
    opciones = {
        "Análisis de impacto de Core Update": "core_update",
        "Análisis de contenido evergreen": "evergreen",
    }

    # Selector (radio o selectbox a gusto)
    seleccion = st.radio(
        "Elige una opción:",
        list(opciones.keys()),
        captions=[
            "Compara métricas antes vs. después de un Core Update.",
            "Evalúa contenido atemporal: vigencia, tráfico y oportunidades."
        ],
        index=0,
    )

    st.session_state["analisis_seleccionado"] = opciones[seleccion]

    # Acción
    col_run, col_note = st.columns([1, 3])
    with col_run:
        if st.button("🚀 Ejecutar análisis", type="primary"):
            run_analysis(opciones[seleccion])
    with col_note:
        st.info(
            "Este demo solo muestra la estructura. "
            "Conectá aquí tus funciones reales (GSC, Sheets, etc.)."
        )

def run_analysis(kind: str):
    st.divider()
    if kind == "core_update":
        run_core_update_demo()
    elif kind == "evergreen":
        run_evergreen_demo()
    else:
        st.error("Análisis no reconocido.")

def run_core_update_demo():
    st.subheader("📈 Análisis de impacto de Core Update")
    st.write(
        "- Define tus fechas **pre** y **post** update.\n"
        "- Trae datos diarios de Search/Discover.\n"
        "- Filtra por país, sección o tipo de fuente.\n"
        "- Exporta a tu plantilla de Google Sheets."
    )
    # 👉 Aquí conectarías tu pipeline real
    with st.expander("Parámetros (demo)"):
        pre_inicio = st.date_input("Fecha pre-inicio")
        post_fin = st.date_input("Fecha post-fin")
        fuente = st.multiselect("Fuente", ["Search", "Discover"], default=["Search"])
        pais = st.text_input("Filtro por país (código ISO, ej: AR, MX, ES)", value="")
        st.caption("Cuando presiones 'Ejecutar', llamá a tu rutina que consulta GSC y exporta.")

def run_evergreen_demo():
    st.subheader("🌲 Análisis de contenido evergreen")
    st.write(
        "- Identifica piezas con tráfico sostenido.\n"
        "- Detecta estacionalidad vs. atemporalidad.\n"
        "- Prioriza refrescos y oportunidades de interlinking."
    )
    # 👉 Aquí conectarías tu pipeline real
    with st.expander("Parámetros (demo)"):
        ventana_meses = st.slider("Ventana de análisis (meses)", 3, 24, 12)
        umbral_trafico = st.number_input("Umbral de tráfico mínimo (visitas/mes)", min_value=0, value=500)
        st.caption("Al ejecutar, consulta tu fuente (GSC/Analytics) y clasifica contenido.")

# ---------------------------
# App
# ---------------------------

user = get_user()
if not user or not getattr(user, "is_logged_in", False):
    login_screen()
else:
    sidebar_user_info(user)
    home_screen(user)
