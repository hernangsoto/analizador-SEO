import streamlit as st
import requests
import pandas as pd
from datetime import date, timedelta

def get_user():
    return getattr(st, "user", getattr(st, "experimental_user", None))

def sidebar_user_info(user):
    with st.sidebar:
        with st.container():
            c1, c2 = st.columns([1, 3])
            with c1:
                pic = getattr(user, "picture", None)
                if pic:
                    try:
                        r = requests.get(pic, timeout=5)
                        if r.status_code == 200:
                            st.image(r.content, width=96)
                    except Exception:
                        pass
            with c2:
                st.header("Información del usuario", anchor=False)
                st.write(f"**Nombre:** {getattr(user, 'name', '—')}")
                st.write(f"**Correo:** {getattr(user, 'email', '—')}")
        st.divider()
        st.button(":material/logout: Cerrar sesión", on_click=st.logout, use_container_width=True)

def login_screen():
    st.header("Esta aplicación es privada.")
    st.subheader("Por favor, inicia sesión.")
    st.button(":material/login: Iniciar sesión con Google", on_click=st.login)

def pick_site(sc_service):
    st.subheader("2) Elegí el sitio a trabajar (Search Console)")
    try:
        site_list = sc_service.sites().list().execute()
        sites = site_list.get("siteEntry", [])
    except Exception as e:
        st.error(f"Error al obtener sitios: {e}")
        st.stop()
    verified = [s for s in sites if s.get("permissionLevel") != "siteUnverifiedUser"]
    if not verified:
        st.error("No se encontraron sitios verificados en esta cuenta.")
        st.stop()
    site_url = st.selectbox("Sitio verificado:", [s["siteUrl"] for s in verified])
    return site_url

def pick_analysis():
    st.subheader("3) Elegí el tipo de análisis")
    opciones = {
        "1. Análisis de entidades (🚧 próximamente)": "1",
        "2. Análisis de tráfico general (🚧 próximamente)": "2",
        "3. Análisis de secciones (🚧 próximamente)": "3",
        "4. Análisis de impacto de Core Update ✅": "4",
        "5. Análisis de tráfico evergreen ✅": "5",
    }
    key = st.radio("Tipos disponibles:", list(opciones.keys()), index=3)
    return opciones[key]

LAG_DAYS_DEFAULT = 3

def params_for_core_update():
    st.markdown("#### Parámetros (Core Update)")
    lag_days = st.number_input("Lag de datos (para evitar días incompletos)", 0, 7, LAG_DAYS_DEFAULT)
    fecha_inicio = st.date_input("¿Cuándo inició el Core Update? (YYYY-MM-DD)")
    termino = st.radio("¿El Core Update ya terminó?", ["sí", "no"], horizontal=True)
    fecha_fin = st.date_input("¿Cuándo finalizó el Core Update? (YYYY-MM-DD)") if termino == "sí" else None
    tipo = st.selectbox("Datos a analizar", ["Search", "Discover", "Ambos"], index=2)
    pais_choice = st.selectbox("¿Filtrar por país? (ISO-3)", ["Todos","ARG","MEX","ESP","USA","COL","PER","CHL","URY"], index=0)
    pais = None if pais_choice == "Todos" else pais_choice
    seccion = st.text_input("¿Limitar a una sección? (path, ej: /vida/)", value="") or None
    return lag_days, fecha_inicio, termino, fecha_fin, tipo, pais, seccion

def params_for_evergreen():
    st.markdown("#### Parámetros (Evergreen)")
    st.caption("Se usa el período más amplio posible de **meses completos** (hasta 16) en Search.")
    lag_days = st.number_input("Lag de datos (para evitar días incompletos)", 0, 7, LAG_DAYS_DEFAULT)
    pais_choice = st.selectbox("¿Filtrar por país? (ISO-3)", ["Todos","ARG","MEX","ESP","USA","COL","PER","CHL","URY"], index=0)
    pais = None if pais_choice == "Todos" else pais_choice
    seccion = st.text_input("¿Limitar a una sección? (path, ej: /vida/)", value="") or None
    incluir_diario = st.checkbox("Incluir análisis diario por URL (lento)", value=False)

    # Ventana de 16 meses completos
    hoy_util = date.today() - timedelta(days=lag_days)
    end_month_first_day = (pd.Timestamp(hoy_util.replace(day=1)) - pd.offsets.MonthBegin(1))
    end_month_last_day = (end_month_first_day + pd.offsets.MonthEnd(0))
    start_month_first_day = (end_month_first_day - pd.DateOffset(months=15))
    start_date = start_month_first_day.date()
    end_date = end_month_last_day.date()
    st.info(f"Ventana mensual: {start_date} → {end_date}")
    return lag_days, pais, seccion, incluir_diario, start_date, end_date

