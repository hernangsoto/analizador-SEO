# modules/ui.py
from __future__ import annotations

import requests
import streamlit as st
import pandas as pd
from datetime import date, timedelta
from typing import Optional, Tuple
from streamlit.errors import StreamlitAuthError

# Valor por defecto para evitar días incompletos en GSC
LAG_DAYS_DEFAULT = 3


# ---------------------------
# Helpers de usuario / sidebar
# ---------------------------
def get_user():
    """Compatibilidad con versiones de Streamlit: st.user o st.experimental_user."""
    return getattr(st, "user", getattr(st, "experimental_user", None))


def get_first_name(full_name: Optional[str]) -> str:
    if not full_name:
        return "👋"
    return full_name.split()[0]


def sidebar_user_info(user) -> None:
    """Panel lateral con foto, nombre, correo y botón de logout."""
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
# Pantalla de login (Auth Streamlit)
# ---------------------------
def login_screen() -> None:
    st.header("Esta aplicación es privada.")
    st.subheader("Por favor, inicia sesión.")

    if st.button(":material/login: Iniciar sesión con Google"):
        try:
            # Si Auth no está configurado, Streamlit lanza StreamlitAuthError.
            st.login()
        except StreamlitAuthError:
            st.error("La autenticación de Streamlit no está configurada para este entorno.")
            st.caption(
                "En Streamlit Cloud: ve a **⋯ → App settings → Authentication**, "
                "activa el login y agrega tus emails o dominio permitido."
            )
        except Exception as e:
            st.error("No se pudo iniciar sesión con Streamlit Auth.")
            st.caption(f"Detalle técnico (debug): {e}")


# ---------------------------
# UI de Search Console y Análisis
# ---------------------------
def pick_site(sc_service) -> str:
    """
    Lista sitios verificados de la cuenta de Search Console conectada.
    Mantiene la selección estable entre reruns usando un key fijo y el valor previo.
    """
    st.subheader("2) Elegí el sitio a trabajar (Search Console)")
    try:
        site_list = sc_service.sites().list().execute()
        sites = site_list.get("siteEntry", [])
    except Exception as e:
        st.error(f"Error al obtener sitios: {e}")
        st.stop()

    # Solo sitios verificados
    verified_urls = [s["siteUrl"] for s in sites if s.get("permissionLevel") != "siteUnverifiedUser"]

    # Orden estable y sin duplicados (sc-domain primero, luego hosts, todo casefold)
    def _sort_key(u: str) -> tuple[int, str]:
        return (0 if u.startswith("sc-domain:") else 1, u.replace("sc-domain:", "").casefold())

    options = sorted(set(verified_urls), key=_sort_key)

    if not options:
        st.error("No se encontraron sitios verificados en esta cuenta.")
        st.stop()

    # Recordar selección previa (si existe) y mantenerla tras el rerun
    prev = st.session_state.get("site_url")
    index = options.index(prev) if prev in options else 0

    site_url = st.selectbox(
        "Sitio verificado:",
        options,
        index=index,
        key="site_url",  # clave estable: mantiene el valor entre reruns
    )
    return site_url


def pick_analysis() -> str:
    """Selector de análisis. Devuelve '4' o '5' (otros pendientes)."""
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


def params_for_core_update() -> Tuple[int, date, str, Optional[date], str, Optional[str], Optional[str]]:
    """UI para parámetros del análisis de Core Update."""
    st.markdown("#### Parámetros (Core Update)")

    lag_days = st.number_input(
        "Lag de datos (para evitar días incompletos)",
        min_value=0, max_value=7, value=LAG_DAYS_DEFAULT
    )

    fecha_inicio = st.date_input("¿Cuándo inició el Core Update? (YYYY-MM-DD)")
    termino = st.radio("¿El Core Update ya terminó?", ["sí", "no"], horizontal=True)
    fecha_fin = st.date_input("¿Cuándo finalizó el Core Update? (YYYY-MM-DD)") if termino == "sí" else None

    tipo = st.selectbox("Datos a analizar", ["Search", "Discover", "Ambos"], index=2)
    pais_choice = st.selectbox(
        "¿Filtrar por país? (ISO-3)",
        ["Todos", "ARG", "MEX", "ESP", "USA", "COL", "PER", "CHL", "URY"],
        index=0,
    )
    pais = None if pais_choice == "Todos" else pais_choice
    seccion = st.text_input("¿Limitar a una sección? (path, ej: /vida/)", value="") or None

    return lag_days, fecha_inicio, termino, fecha_fin, tipo, pais, seccion


def params_for_evergreen():
    """UI para parámetros del análisis Evergreen + cálculo de ventana mensual (hasta 16 meses)."""
    st.markdown("#### Parámetros (Evergreen)")
    st.caption("Se usa el período más amplio posible de **meses completos** (hasta 16) en Search.")

    lag_days = st.number_input(
        "Lag de datos (para evitar días incompletos)",
        min_value=0, max_value=7, value=LAG_DAYS_DEFAULT
    )

    pais_choice = st.selectbox(
        "¿Filtrar por país? (ISO-3)",
        ["Todos", "ARG", "MEX", "ESP", "USA", "COL", "PER", "CHL", "URY"],
        index=0,
    )
    pais = None if pais_choice == "Todos" else pais_choice
    seccion = st.text_input("¿Limitar a una sección? (path, ej: /vida/)", value="") or None
    incluir_diario = st.checkbox("Incluir análisis diario por URL (lento)", value=False)

    # Ventana de 16 meses completos terminando en el último mes cerrado
    hoy_util = date.today() - timedelta(days=lag_days)
    end_month_first_day = (pd.Timestamp(hoy_util.replace(day=1)) - pd.offsets.MonthBegin(1))
    end_month_last_day = (end_month_first_day + pd.offsets.MonthEnd(0))
    start_month_first_day = (end_month_first_day - pd.DateOffset(months=15))
    start_date = start_month_first_day.date()
    end_date = end_month_last_day.date()

    st.info(f"Ventana mensual: {start_date} → {end_date}")

    return lag_days, pais, seccion, incluir_diario, start_date, end_date
